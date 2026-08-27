/**
 * ControlBar — compact two-row toolbar containing all wizard controls.
 * Replaces the old InputPanel + SearchPanel cards with a flat toolbar layout.
 * All wizard logic (state, handlers) lives in LayoutContainer; this is pure UI.
 *
 * CHANGE: Disambiguation list now renders as a dropdown (custom select-style
 * overlay) instead of a flat button list, keeping the toolbar compact while
 * still showing all matches.
 *
 * CHANGE: Reporting Date field is a dropdown of the actual submission dates
 * on file for the selected return/table (fetched by LayoutContainer via
 * GET /variance/dates), instead of a free calendar — the user picks a date
 * guaranteed to have data rather than guessing one.
 */
import { useEffect, useRef, useState } from 'react'
import { VARIANCE_STEPS, COMPARISON_MODES, freqLabel } from '../types.js'

const SCORE_BADGE = (score) => {
  if (score >= 100) return { label: 'Exact', cls: 'score-exact' }
  if (score >= 90)  return { label: 'High',  cls: 'score-high' }
  if (score >= 75)  return { label: 'Contains', cls: 'score-contains' }
  return { label: 'Partial', cls: 'score-partial' }
}

// ── Disambiguation Dropdown ────────────────────────────────────────────────────
function DisambigDropdown({ candidates, returnName, onSelect, onCancel }) {
  const [open, setOpen]   = useState(true)   // open by default when rendered
  const [filter, setFilter] = useState('')
  const dropRef = useRef(null)

  // Close on outside click
  useEffect(() => {
    function handleClickOutside(e) {
      if (dropRef.current && !dropRef.current.contains(e.target)) {
        setOpen(false)
        onCancel()
      }
    }
    if (open) document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [open, onCancel])

  // Filter candidates by typed text
  const filtered = filter.trim()
    ? candidates.filter((c) =>
        c.return_name.toLowerCase().includes(filter.toLowerCase())
      )
    : candidates

  function handleSelect(c) {
    setOpen(false)
    onSelect(c)
  }

  return (
    <div className="disambig-dropdown-wrap" ref={dropRef}>
      {/* Trigger button — shows how many matches */}
      <button
        className="disambig-trigger"
        onClick={() => setOpen((o) => !o)}
        type="button"
      >
        <span className="disambig-trigger-icon">⚡</span>
        <span className="disambig-trigger-label">
          {candidates.length} match{candidates.length !== 1 ? 'es' : ''} for&nbsp;
          <strong>&ldquo;{returnName}&rdquo;</strong>
        </span>
        <span className="disambig-trigger-arrow">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="disambig-menu">
          {/* Search/filter inside dropdown */}
          <div className="disambig-search-row">
            <input
              className="disambig-search-input"
              placeholder="Filter matches…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              autoFocus
            />
            <button
              className="disambig-cancel-btn"
              onClick={() => { setOpen(false); onCancel() }}
              title="Cancel"
              type="button"
            >
              ✕
            </button>
          </div>

          {/* Candidate list */}
          <div className="disambig-list">
            {filtered.length === 0 ? (
              <div className="disambig-empty">No matches for &ldquo;{filter}&rdquo;</div>
            ) : (
              filtered.map((c) => {
                const badge = SCORE_BADGE(c.score)
                return (
                  <button
                    key={c.return_id}
                    className="disambig-item"
                    onClick={() => handleSelect(c)}
                    type="button"
                  >
                    <span className={`ctrl-score-badge ${badge.cls}`}>
                      {badge.label}
                    </span>
                    <span className="disambig-item-name">{c.return_name}</span>
                    <span className="disambig-item-meta">
                      {freqLabel(c.report_freq) || '—'}
                    </span>
                    <span className="disambig-item-id">#{c.return_id}</span>
                  </button>
                )
              })
            )}
          </div>

          <div className="disambig-footer">
            {filtered.length} of {candidates.length} shown
          </div>
        </div>
      )}
    </div>
  )
}

// ── NLP "tell me more" clarification panel ───────────────────────────────────
// Rendered directly below the NLP mini-bar when the return is known but the
// specific table/section is unclear (dimension === "table") — see
// LayoutContainer's handleNlpSearch / backend/main.py's needs_clarification
// response. Deliberately does NOT list the candidate tables as pickable
// options — those are internal schema names, not something to show a
// business user. Instead it's always just a free-text box: whatever the
// user types is folded into the original query and the whole thing is
// re-resolved from scratch via onOthers (same mechanism as NlpReturnPicker's
// "Others" box above), alongside a Skip that proceeds with the best-effort
// RAG resolution immediately.
function NlpClarificationPanel({ clarification, onSkip, onCancel, onOthers }) {
  const [text, setText] = useState('')
  const { question, skippable } = clarification

  function handleSubmit() {
    if (!text.trim()) return
    onOthers(text)
  }

  return (
    <div className="nlp-clarify-panel">
      <div className="nlp-clarify-question">{question}</div>
      <div className="nlp-clarify-options">
        <input
          className="disambig-search-input"
          placeholder="Describe the data you need…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
          autoFocus
        />
        <button
          type="button"
          className="disambig-others-submit-btn"
          onClick={handleSubmit}
          disabled={!text.trim()}
          title="Submit"
        >
          &#8594;
        </button>
        {skippable && (
          <button type="button" className="nlp-clarify-skip-btn" onClick={onSkip}>
            Skip — best guess
          </button>
        )}
        <button
          type="button"
          className="nlp-clarify-cancel-btn"
          onClick={onCancel}
          title="Cancel"
        >
          ✕
        </button>
      </div>
    </div>
  )
}

// ── NLP "which return" clarification panel ──────────────────────────────────
// Rendered below the NLP mini-bar when the query gave no usable table/return
// signal at all (dimension === "return"). Options are pre-narrowed by the
// backend to just the returns the query itself gave some signal for (a named
// mention, or its own low-confidence embedding shortlist) — see
// backend/main.py's _build_return_clarification — so this is normally a
// short list, not every authorized return; it only falls back to the full
// list as a last resort when the query matched nothing at all. Reuses
// DisambigDropdown's searchable trigger+menu structure/styles, generalized
// to plain {id, label} items instead of return-shaped candidates with a
// score badge.
//
// When `clarification.allowOther` is set, an "Others" row lets the user type
// free-text extra detail instead of picking a listed return — submitting it
// calls `onOthers(text)`, which the caller (LayoutContainer) folds into the
// original query and re-resolves from scratch.
function NlpReturnPicker({ clarification, onSelect, onSkip, onCancel, onOthers }) {
  const [open, setOpen] = useState(true)
  const [filter, setFilter] = useState('')
  const [showOthers, setShowOthers] = useState(false)
  const [othersText, setOthersText] = useState('')
  const dropRef = useRef(null)
  const { question, options, skippable, allowOther } = clarification

  useEffect(() => {
    function handleClickOutside(e) {
      if (dropRef.current && !dropRef.current.contains(e.target)) {
        setOpen(false)
        onCancel()
      }
    }
    if (open) document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [open, onCancel])

  const filtered = filter.trim()
    ? options.filter((o) => o.label.toLowerCase().includes(filter.toLowerCase()))
    : options

  function handleSelect(opt) {
    setOpen(false)
    onSelect(opt)
  }

  function handleOthersSubmit() {
    if (!othersText.trim()) return
    setOpen(false)
    onOthers(othersText)
  }

  return (
    <div className="nlp-clarify-panel">
      <div className="nlp-clarify-question">{question}</div>
      <div className="disambig-dropdown-wrap" ref={dropRef}>
        <button className="disambig-trigger" onClick={() => setOpen((o) => !o)} type="button">
          <span className="disambig-trigger-icon">⚡</span>
          <span className="disambig-trigger-label">
            {options.length} return{options.length !== 1 ? 's' : ''} available
          </span>
          <span className="disambig-trigger-arrow">{open ? '▲' : '▼'}</span>
        </button>

        {open && (
          <div className="disambig-menu">
            <div className="disambig-search-row">
              <input
                className="disambig-search-input"
                placeholder="Filter returns…"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                autoFocus
              />
              {skippable && (
                <button
                  type="button"
                  className="nlp-clarify-skip-btn"
                  onClick={() => { setOpen(false); onSkip() }}
                >
                  Skip
                </button>
              )}
              <button
                className="disambig-cancel-btn"
                onClick={() => { setOpen(false); onCancel() }}
                title="Cancel"
                type="button"
              >
                ✕
              </button>
            </div>

            <div className="disambig-list">
              {filtered.length === 0 ? (
                <div className="disambig-empty">No matches for &ldquo;{filter}&rdquo;</div>
              ) : (
                filtered.map((opt) => (
                  <button
                    key={opt.id}
                    className="disambig-item"
                    onClick={() => handleSelect(opt)}
                    type="button"
                  >
                    <span className="disambig-item-name">{opt.label}</span>
                  </button>
                ))
              )}
            </div>

            <div className="disambig-footer">
              {filtered.length} of {options.length} shown
            </div>

            {allowOther && (
              <div className="disambig-others-row">
                {!showOthers ? (
                  <button
                    type="button"
                    className="disambig-item disambig-others-toggle"
                    onClick={() => setShowOthers(true)}
                  >
                    <span className="disambig-item-name">
                      Others &mdash; none of these, let me describe it
                    </span>
                  </button>
                ) : (
                  <div className="disambig-others-input-row">
                    <input
                      className="disambig-search-input"
                      placeholder="e.g. return name, section, or more detail…"
                      value={othersText}
                      onChange={(e) => setOthersText(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && handleOthersSubmit()}
                      autoFocus
                    />
                    <button
                      type="button"
                      className="disambig-others-submit-btn"
                      onClick={handleOthersSubmit}
                      disabled={!othersText.trim()}
                      title="Submit"
                    >
                      &#8594;
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Reporting Date field — dropdown of dates that actually have data ───────
function DateField({ dateStr, setDateStr, availableDates, datesLoading }) {
  if (datesLoading) {
    return (
      <select className="ctrl-select ctrl-input-date" disabled value="">
        <option value="">Loading dates&hellip;</option>
      </select>
    )
  }

  if (!availableDates || availableDates.length === 0) {
    return (
      <select className="ctrl-select ctrl-input-date" disabled value="">
        <option value="">No data found for this table</option>
      </select>
    )
  }

  return (
    <select
      className="ctrl-select ctrl-input-date"
      value={dateStr}
      onChange={(e) => setDateStr(e.target.value)}
      title="Only dates with actual submitted data are listed"
    >
      <option value="" disabled>
        Select a date&hellip;
      </option>
      {availableDates.map((d) => (
        <option key={d} value={d}>
          {d}
        </option>
      ))}
    </select>
  )
}

// ── Main ControlBar ────────────────────────────────────────────────────────────
export default function ControlBar({
  step,
  returnName,
  setReturnName,
  returnInfo,
  tables,
  tableName,
  setTableName,
  dateStr,
  setDateStr,
  availableDates,
  datesLoading,
  periods,
  setPeriods,
  comparisonMode,
  setComparisonMode,
  loading,
  error,
  candidates,
  handleFindReturn,
  handleCompute,
  handleReset,
  handleSelectCandidate,
  nlpQuery,
  setNlpQuery,
  handleNlpSearch,
  handleVoiceInput,
  nlpClarification,
  onClarificationSelect,
  onClarificationSkip,
  onClarificationCancel,
  onClarificationOthers,
}) {
  const canCompute = !!(
    returnInfo &&
    tableName &&
    dateStr.trim() &&
    !loading
  )

  const isResult  = step === VARIANCE_STEPS.RESULT
  const searchBusy = loading && !returnInfo
  const showDisambig =
    step === VARIANCE_STEPS.DISAMBIGUATE &&
    candidates != null &&
    candidates.length > 0

  return (
    <div className="ctrl-bar">

      {/* NLP bar */}
      <div className="nlp-mini-bar">
        <input
          type="text"
          className="nlp-mini-input"
          placeholder="Ask..."
          value={nlpQuery}
          onChange={(e) => setNlpQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && nlpQuery.trim()) handleNlpSearch(nlpQuery)
          }}
        />
        <button
          type="button"
          className="nlp-mini-btn"
          onClick={handleVoiceInput}
          title="Voice Input"
        >
          🎤
        </button>
        <button
          type="button"
          className="nlp-mini-btn nlp-search"
          onClick={() => nlpQuery.trim() && handleNlpSearch(nlpQuery)}
          title="Search"
        >
          🔍
        </button>
      </div>

      {nlpClarification && nlpClarification.dimension === 'return' && (
        <NlpReturnPicker
          clarification={nlpClarification}
          onSelect={onClarificationSelect}
          onSkip={onClarificationSkip}
          onCancel={onClarificationCancel}
          onOthers={onClarificationOthers}
        />
      )}
      {nlpClarification && nlpClarification.dimension !== 'return' && (
        <NlpClarificationPanel
          clarification={nlpClarification}
          onSkip={onClarificationSkip}
          onCancel={onClarificationCancel}
          onOthers={onClarificationOthers}
        />
      )}

      {/* Row 1: Return search */}
      <div className="ctrl-row">
        <span className="ctrl-label">Return</span>

        <input
          className="ctrl-input"
          value={returnName}
          onChange={(e) => setReturnName(e.target.value)}
          onKeyDown={(e) =>
            e.key === 'Enter' && !loading && handleFindReturn()
          }
          placeholder="e.g. CIMS, RAQ, BSR1"
          disabled={loading && !returnInfo}
        />

        <button
          className="btn btn-sm"
          disabled={!returnName.trim() || loading}
          onClick={handleFindReturn}
        >
          {searchBusy ? (
            <>
              <span className="spinner" />
              &thinsp;Searching&hellip;
            </>
          ) : (
            'Search'
          )}
        </button>

        {/* ── Disambiguation dropdown — inline in Row 1 ── */}
        {showDisambig && (
          <DisambigDropdown
            candidates={candidates}
            returnName={returnName}
            onSelect={handleSelectCandidate}
            onCancel={handleReset}
          />
        )}

        {returnInfo && (
          <>
            <div className="ctrl-sep" aria-hidden="true" />
            <div className="ctrl-return-tag">
              <span className="ctrl-return-name">{returnInfo.return_name}</span>
              <span className="vt-badge vt-badge-curr">
                {freqLabel(returnInfo.report_freq)}
              </span>
              <span className="vt-badge vt-badge-prev">
                {(returnInfo.tables || []).length}&thinsp;table
                {(returnInfo.tables || []).length !== 1 ? 's' : ''}
              </span>
            </div>
          </>
        )}

        {error && (
          <span className="ctrl-error-inline">
            &#x26A0;&thinsp;{error}
          </span>
        )}
      </div>

      {/* Row 2: Query config — only visible once a return is selected */}
      {returnInfo && (
        <div className="ctrl-row">

          <span className="ctrl-label">Table</span>
          <select
            className="ctrl-select"
            value={tableName}
            onChange={(e) => setTableName(e.target.value)}
          >
            {tables.map((t) => (
              <option key={t.table_name} value={t.table_name}>
                {t.table_name}
              </option>
            ))}
          </select>

          <div className="ctrl-sep" aria-hidden="true" />

          <span className="ctrl-label">Date</span>
          <DateField
            dateStr={dateStr}
            setDateStr={setDateStr}
            availableDates={availableDates}
            datesLoading={datesLoading}
          />

          <div className="ctrl-sep" aria-hidden="true" />

          <span className="ctrl-label">Periods</span>
          <div className="ctrl-chips">
            {[1, 2, 3].map((n) => (
              <button
                key={n}
                className={
                  'ctrl-chip' + (periods === n ? ' ctrl-chip-on' : '')
                }
                onClick={() => setPeriods(n)}
                title={n + ' comparison period' + (n > 1 ? 's' : '')}
              >
                {n}
              </button>
            ))}
          </div>

          {periods > 1 && (
            <>
              <div className="ctrl-sep" aria-hidden="true" />
              <span className="ctrl-label">Compare</span>
              <div className="ctrl-chips-compare">
                <button
                  className={'ctrl-chip-compare ctrl-chip' + (comparisonMode === COMPARISON_MODES.VS_CURRENT ? ' ctrl-chip-on' : '')}
                  onClick={() => setComparisonMode(COMPARISON_MODES.VS_CURRENT)}
                  title="Compare every previous period directly against the current period"
                >
                  vs Current
                </button>
                <button
                  className={'ctrl-chip-compare ctrl-chip' + (comparisonMode === COMPARISON_MODES.SEQUENTIAL ? ' ctrl-chip-on' : '')}
                  onClick={() => setComparisonMode(COMPARISON_MODES.SEQUENTIAL)}
                  title="Compare each period to the one immediately before it (chained)"
                >
                  Seq
                </button>
              </div>
            </>
          )}

          <div className="ctrl-sep" aria-hidden="true" />

          <div className="ctrl-actions">
            {!isResult ? (
              <button
                className="btn btn-sm"
                disabled={!canCompute}
                onClick={handleCompute}
              >
                {loading ? (
                  <><span className="spinner" />&thinsp;Computing&hellip;</>
                ) : (
                  'Compute Variance'
                )}
              </button>
            ) : (
              <button
                className="btn btn-sm btn-secondary"
                disabled={loading}
                onClick={handleCompute}
                title="Re-run with current parameters"
              >
                {loading ? (
                  <><span className="spinner" />&thinsp;Recomputing&hellip;</>
                ) : (
                  'Recompute'
                )}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}