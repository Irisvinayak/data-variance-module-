/**
 * ControlBar — compact two-row toolbar containing all wizard controls.
 * All wizard logic (state, handlers) lives in LayoutContainer; this is pure UI.
 *
 * The disambiguation list is a select-style dropdown, keeping the toolbar
 * compact while still showing all matches. The Reporting Date field lists only
 * the submission dates on file for the selected return/table (fetched by
 * LayoutContainer via GET /variance/dates), so the user picks a date
 * guaranteed to have data rather than guessing one on a calendar.
 */
import { useRef, useState } from 'react'
import { PropTypes, candidateShape, clarificationShape, interpretationShape, returnInfoShape } from '../propTypes.js'
import { VARIANCE_STEPS, COMPARISON_MODES, freqLabel } from '../types.js'
import useClickOutside from '../hooks/useClickOutside.js'
import useListboxNav from '../hooks/useListboxNav.js'

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

  useClickOutside(dropRef, open, () => {
    setOpen(false)
    onCancel()
  })

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

  // Arrow keys / Home / End / Enter over the filtered list; focus stays in the filter input.
  const nav = useListboxNav(filtered, handleSelect)

  return (
    <div
      className="disambig-dropdown-wrap"
      ref={dropRef}
      onKeyDown={(e) => { if (e.key === 'Escape' && open) { setOpen(false); onCancel() } }}
    >
      {/* Trigger button — shows how many matches */}
      <button
        className="disambig-trigger"
        onClick={() => setOpen((o) => !o)}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
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
              aria-label="Filter matches"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              autoFocus
              {...nav.inputProps}
            />
            <button
              className="disambig-cancel-btn"
              onClick={() => { setOpen(false); onCancel() }}
              title="Cancel"
              aria-label="Cancel"
              type="button"
            >
              ✕
            </button>
          </div>

          {/* Candidate list */}
          <div className="disambig-list" aria-label="Matching returns" {...nav.listProps}>
            {filtered.length === 0 ? (
              <div className="disambig-empty">No matches for &ldquo;{filter}&rdquo;</div>
            ) : (
              filtered.map((c, i) => {
                const badge = SCORE_BADGE(c.score)
                return (
                  <button
                    key={c.return_id}
                    className={'disambig-item' + (i === nav.active ? ' disambig-item-active' : '')}
                    onClick={() => handleSelect(c)}
                    type="button"
                    {...nav.optionProps(i)}
                  >
                    <span className={`ctrl-score-badge ${badge.cls}`}>
                      {badge.label}
                    </span>
                    <span className="disambig-item-name">{c.return_name}</span>
                    <span className="disambig-item-meta">
                      {freqLabel(c.report_freq)}
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

// ── NLP "here's what I understood" chips ─────────────────────────────────────
// Rendered under the NLP bar after any resolve (result OR clarification),
// from the `interpretation` object /variance/nlresolve now returns — see
// backend/nlp/query_analyzer.QueryAnalysis.to_interpretation().
//
// Why this is here at all: the NLP path silently makes four consequential
// decisions — which return, which table/column, which reporting date, and how
// many comparison periods — and until now the ONLY evidence the user had that
// any of them were right was whether the numbers on screen looked plausible.
// A query for domestic data answered from the overseas return, or a "last 2
// quarters" read as a single period, renders as a perfectly normal-looking
// table. Showing the interpretation makes a wrong resolution visible
// immediately, at the moment the user can still correct it by rephrasing.
function NlpInterpretation({ interpretation }) {
  if (!interpretation) return null

  const {
    metric_text: metric,
    return_names: returnNames,
    date_text: dateText,
    scope,
    resolved_columns: resolvedColumns,
    resolved_column_labels: resolvedColumnLabels,
    reporting_date: reportingDate,
    comparison_periods: comparisonPeriods,
    comparison_dates: comparisonDates,
    date_notes: dateNotes,
  } = interpretation

  const SCOPE_LABELS = { DOM: 'Domestic', OVE: 'Overseas', GLOBAL: 'Global' }

  const chips = [
    returnNames?.length && { key: 'return', label: 'Return', value: returnNames.join(' / ') },
    metric && { key: 'metric', label: 'Looking for', value: metric },
    // Prefer the human labels from schema.json over the raw column
    // identifiers: "Total Loan Assets" is what the user asked for, whereas
    // TOTAL_LOAN_ASSETS is a schema detail they have no way to interpret.
    (resolvedColumnLabels?.length || resolvedColumns?.length) && {
      key: 'columns',
      label: 'Matched',
      value: (resolvedColumnLabels?.length ? resolvedColumnLabels : resolvedColumns).join(', '),
    },
    scope && { key: 'scope', label: 'Scope', value: SCOPE_LABELS[scope] || scope },
    // Prefer the RESOLVED date over the raw phrase: "last 2 quarters" is what
    // the user typed, "30-JUN-2025, 2 periods" is what it actually became
    // after being resolved against the data that exists. The raw phrase is
    // only shown when resolution hasn't happened yet (a clarification round).
    reportingDate
      ? {
          key: 'date',
          label: 'Period',
          // The exact dates compared, when the backend sent them; they can
          // differ from the phrase (a named date with no data, the 3-date cap).
          value:
            comparisonDates?.length > 1
              ? `${reportingDate} vs ${comparisonDates.slice(1).join(', ')}`
              : comparisonPeriods > 1
                ? `${reportingDate} + ${comparisonPeriods} prior`
                : reportingDate,
        }
      : dateText && { key: 'date', label: 'Period', value: dateText },
  ].filter(Boolean)

  if (chips.length === 0) return null

  return (
    <div className="nlp-interpretation" title="How your question was understood">
      <span className="nlp-interpretation-lead">Understood as</span>
      {chips.map((c) => (
        <span key={c.key} className="nlp-interpretation-chip">
          <span className="nlp-interpretation-chip-label">{c.label}</span>
          <span className="nlp-interpretation-chip-value">{c.value}</span>
        </span>
      ))}
      {/* Every place the dates used differ from the dates asked for. */}
      {dateNotes?.map((note, i) => (
        <span key={`${i}:${note}`} className="nlp-interpretation-chip nlp-interpretation-note">
          <span className="nlp-interpretation-chip-label">Note</span>
          <span className="nlp-interpretation-chip-value">{note}</span>
        </span>
      ))}
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

  useClickOutside(dropRef, open, () => {
    setOpen(false)
    onCancel()
  })

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

  const nav = useListboxNav(filtered, handleSelect)

  return (
    <div className="nlp-clarify-panel">
      <div className="nlp-clarify-question">{question}</div>
      <div
        className="disambig-dropdown-wrap"
        ref={dropRef}
        onKeyDown={(e) => { if (e.key === 'Escape' && open) { setOpen(false); onCancel() } }}
      >
        <button
          className="disambig-trigger"
          onClick={() => setOpen((o) => !o)}
          type="button"
          aria-haspopup="listbox"
          aria-expanded={open}
        >
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
                aria-label="Filter returns"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                autoFocus
                {...nav.inputProps}
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
                aria-label="Cancel"
                type="button"
              >
                ✕
              </button>
            </div>

            <div className="disambig-list" aria-label="Available returns" {...nav.listProps}>
              {filtered.length === 0 ? (
                <div className="disambig-empty">No matches for &ldquo;{filter}&rdquo;</div>
              ) : (
                filtered.map((opt, i) => (
                  <button
                    key={opt.id}
                    className={'disambig-item' + (i === nav.active ? ' disambig-item-active' : '')}
                    onClick={() => handleSelect(opt)}
                    type="button"
                    {...nav.optionProps(i)}
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

// ── Reporting Date field — checkbox list of dates that actually have data ──
// Multi-select, capped at MAX_DATES. Selecting dates here is a DIFFERENT
// question from the Periods chips, not an addition to them:
//   • Periods = "start at this date and walk back N periods by the calendar"
//   • Checkboxes = "compare exactly these dates"
// The second is what a user wants when a return was filed off-cycle, or when
// the periods of interest aren't consecutive. The two cannot both apply, so
// ControlBar hides the Periods/Compare controls entirely once any box is
// ticked (see the `usingDateSelection` branch below) rather than showing a
// control that silently has no effect.
//
// The newest ticked date is the current period and the rest are its
// comparisons — the same shape the Periods path produces, so the result table
// is unchanged. That's stated in the summary line so it isn't a hidden rule.
const MAX_DATES = 3

function DateField({ selectedDates, setSelectedDates, availableDates, datesLoading, datesError, onRetryDates }) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef(null)

  useClickOutside(wrapRef, open, () => setOpen(false))

  if (datesLoading) {
    return (
      <button className="ctrl-select ctrl-input-date" disabled type="button">
        Loading dates&hellip;
      </button>
    )
  }

  if (datesError) {
    return (
      <button className="ctrl-select ctrl-input-date" type="button" role="alert" onClick={onRetryDates}>
        Could not load dates &mdash; click to retry
      </button>
    )
  }

  if (!availableDates || availableDates.length === 0) {
    return (
      <button className="ctrl-select ctrl-input-date" disabled type="button">
        No data found for this table
      </button>
    )
  }

  const atLimit = selectedDates.length >= MAX_DATES

  function toggle(d) {
    setSelectedDates((prev) => {
      if (prev.includes(d)) return prev.filter((x) => x !== d)
      // Silently ignoring the click at the cap would look like a broken
      // checkbox; the input is disabled instead (see `disabled` below), so
      // this guard is only a safety net for keyboard/programmatic paths.
      if (prev.length >= MAX_DATES) return prev
      // Kept in availableDates' own order (newest first, as
      // GET /variance/dates returns them) rather than click order, so the
      // summary line and the backend's "newest is current" rule agree with
      // what the user sees in the list.
      return availableDates.filter((x) => x === d || prev.includes(x))
    })
  }

  const summary =
    selectedDates.length === 0
      ? 'Select date(s)…'
      : selectedDates.length === 1
        ? selectedDates[0]
        : `${selectedDates[0]} +${selectedDates.length - 1} to compare`

  return (
    <div className="ctrl-date-wrap" ref={wrapRef}>
      <button
        type="button"
        className={'ctrl-select ctrl-input-date' + (selectedDates.length ? ' ctrl-input-date-on' : '')}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="true"
        aria-expanded={open}
        title={
          selectedDates.length > 1
            ? `Newest (${selectedDates[0]}) is the current period; the rest are compared against it`
            : 'Only dates with actual submitted data are listed'
        }
      >
        <span className="ctrl-date-summary">{summary}</span>
        <span className="ctrl-date-arrow">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="ctrl-date-menu">
          <div className="ctrl-date-menu-head">
            <span>
              {selectedDates.length}/{MAX_DATES} selected
            </span>
            {selectedDates.length > 0 && (
              <button
                type="button"
                className="ctrl-date-clear"
                onClick={() => setSelectedDates([])}
              >
                Clear
              </button>
            )}
          </div>

          <div className="ctrl-date-list">
            {availableDates.map((d) => {
              const checked = selectedDates.includes(d)
              return (
                <label
                  key={d}
                  className={
                    'ctrl-date-item' +
                    (checked ? ' ctrl-date-item-on' : '') +
                    (!checked && atLimit ? ' ctrl-date-item-disabled' : '')
                  }
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={!checked && atLimit}
                    onChange={() => toggle(d)}
                  />
                  <span>{d}</span>
                </label>
              )
            })}
          </div>

          {selectedDates.length > 1 && (
            <div className="ctrl-date-note">
              <strong>{selectedDates[0]}</strong> is the current period; the other{' '}
              {selectedDates.length - 1 === 1 ? 'date is' : 'dates are'} compared against it.
            </div>
          )}
          {atLimit && (
            <div className="ctrl-date-note">
              Maximum {MAX_DATES} dates — untick one to choose another.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main ControlBar ────────────────────────────────────────────────────────────
// Voice input is not implemented (handler is a no-op); keep the button hidden.
const SHOW_VOICE_INPUT = false

export default function ControlBar({
  step,
  returnName,
  setReturnName,
  returnInfo,
  tables,
  tableName,
  setTableName,
  selectedDates,
  setSelectedDates,
  availableDates,
  datesLoading,
  datesError,
  onRetryDates,
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
  nlpInterpretation,
  onClarificationSelect,
  onClarificationSkip,
  onClarificationCancel,
  onClarificationOthers,
}) {
  // TWO OR MORE ticked dates means the user has named the exact periods to
  // compare, so the Periods/Compare controls come off screen (see the JSX
  // below) — they would have no effect. A single ticked date is the ordinary
  // case and behaves exactly as before: it is the starting point that Periods
  // walks back from, so those controls stay.
  const usingDateSelection = selectedDates.length > 1
  const canCompute = !!(
    returnInfo &&
    tableName &&
    selectedDates.length > 0 &&
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
          aria-label="Ask a question about your data"
          value={nlpQuery}
          onChange={(e) => setNlpQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && nlpQuery.trim() && !loading) handleNlpSearch(nlpQuery)
          }}
        />
        {SHOW_VOICE_INPUT && (
          <button
            type="button"
            className="nlp-mini-btn"
            onClick={handleVoiceInput}
            title="Voice Input"
          >
            🎤
          </button>
        )}
        <button
          type="button"
          className="nlp-mini-btn nlp-search"
          onClick={() => nlpQuery.trim() && handleNlpSearch(nlpQuery)}
          disabled={loading}
          title="Search"
          aria-label="Ask the question"
        >
          🔍
        </button>
      </div>

      <NlpInterpretation interpretation={nlpInterpretation} />

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
          aria-label="Return name"
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
            selectedDates={selectedDates}
            setSelectedDates={setSelectedDates}
            availableDates={availableDates}
            datesLoading={datesLoading}
            datesError={datesError}
            onRetryDates={onRetryDates}
          />

          {/* Periods/Compare are hidden — not merely ignored — once dates are
              ticked: the checkbox selection already states exactly which
              periods to compare, so a visible "walk back N periods" control
              that has no effect would be worse than no control at all. */}
          {!usingDateSelection && (
          <>
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

DisambigDropdown.propTypes = {
  candidates: PropTypes.arrayOf(candidateShape).isRequired,
  returnName: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
}

NlpReturnPicker.propTypes = {
  clarification: clarificationShape.isRequired,
  onSelect: PropTypes.func.isRequired,
  onSkip: PropTypes.func,
  onCancel: PropTypes.func.isRequired,
  onOthers: PropTypes.func,
}

NlpClarificationPanel.propTypes = {
  clarification: clarificationShape.isRequired,
  onSkip: PropTypes.func,
  onCancel: PropTypes.func.isRequired,
  onOthers: PropTypes.func,
}

NlpInterpretation.propTypes = {
  interpretation: interpretationShape,
}

DateField.propTypes = {
  selectedDates: PropTypes.arrayOf(PropTypes.string).isRequired,
  setSelectedDates: PropTypes.func.isRequired,
  availableDates: PropTypes.arrayOf(PropTypes.string),
  datesLoading: PropTypes.bool,
  datesError: PropTypes.bool,
  onRetryDates: PropTypes.func,
}

ControlBar.propTypes = {
  step: PropTypes.string,
  returnName: PropTypes.string,
  setReturnName: PropTypes.func.isRequired,
  returnInfo: returnInfoShape,
  tables: PropTypes.array,
  tableName: PropTypes.string,
  setTableName: PropTypes.func.isRequired,
  selectedDates: PropTypes.arrayOf(PropTypes.string).isRequired,
  setSelectedDates: PropTypes.func.isRequired,
  availableDates: PropTypes.arrayOf(PropTypes.string),
  datesLoading: PropTypes.bool,
  datesError: PropTypes.bool,
  onRetryDates: PropTypes.func,
  periods: PropTypes.number,
  setPeriods: PropTypes.func.isRequired,
  comparisonMode: PropTypes.string,
  setComparisonMode: PropTypes.func.isRequired,
  loading: PropTypes.bool,
  error: PropTypes.string,
  candidates: PropTypes.arrayOf(candidateShape),
  handleFindReturn: PropTypes.func.isRequired,
  handleCompute: PropTypes.func.isRequired,
  handleReset: PropTypes.func.isRequired,
  handleSelectCandidate: PropTypes.func.isRequired,
  nlpQuery: PropTypes.string,
  setNlpQuery: PropTypes.func.isRequired,
  handleNlpSearch: PropTypes.func.isRequired,
  handleVoiceInput: PropTypes.func,
  nlpClarification: clarificationShape,
  nlpInterpretation: interpretationShape,
  onClarificationSelect: PropTypes.func,
  onClarificationSkip: PropTypes.func,
  onClarificationCancel: PropTypes.func,
  onClarificationOthers: PropTypes.func,
}
