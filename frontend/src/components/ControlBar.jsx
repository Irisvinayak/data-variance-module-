/**
 * ControlBar — compact two-row toolbar containing all wizard controls.
 * Replaces the old InputPanel + SearchPanel cards with a flat toolbar layout.
 * All wizard logic (state, handlers) lives in LayoutContainer; this is pure UI.
 *
 * CHANGE: Disambiguation list now renders as a dropdown (custom select-style
 * overlay) instead of a flat button list, keeping the toolbar compact while
 * still showing all matches.
 *
 * CHANGE: Reporting Date field now uses flatpickr instead of a plain text
 * input. The picker is initialized/torn down whenever Row 2 actually mounts
 * (i.e. when returnInfo becomes available), since this row doesn't exist in
 * the DOM until then — initializing on first component mount alone would
 * silently fail to attach, since the ref would still be null at that point.
 */
import { useEffect, useRef, useState } from 'react'
import flatpickr from 'flatpickr'
import 'flatpickr/dist/flatpickr.min.css'
import { VARIANCE_STEPS, freqLabel } from '../types.js'

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

// ── Reporting Date field (flatpickr-backed, single input, no future dates) ──
function DateField({ dateStr, setDateStr, dateHint }) {
  const inputRef = useRef(null)
  const fpRef = useRef(null)

  // Init/teardown flatpickr directly on the input itself (no wrap div needed).
  // This component only mounts once returnInfo is set (see Row 2 below), so
  // by the time this effect runs, inputRef.current is guaranteed to exist.
  useEffect(() => {
    if (inputRef.current && !fpRef.current) {
      fpRef.current = flatpickr(inputRef.current, {
        dateFormat: 'd-M-Y',
        defaultDate: dateStr || null,
        maxDate: 'today', // disallow picking a future date
        onChange: (_selectedDates, selectedDate) => {
          setDateStr(selectedDate)
        },
      })
    }

    return () => {
      if (fpRef.current) {
        fpRef.current.destroy()
        fpRef.current = null
      }
    }
  }, [])

  // Keep the picker's internal display in sync if dateStr changes
  // externally (e.g. handleReset clearing it back to '').
  useEffect(() => {
    if (fpRef.current) {
      fpRef.current.setDate(dateStr || null, false)
    }
  }, [dateStr])

  return (
    <input
      ref={inputRef}
      type="text"
      className="ctrl-input ctrl-input-date"
      placeholder={dateHint.example || 'DD-MMM-YYYY'}
      readOnly
      title={
        'Format: DD-MMM-YYYY' +
        (dateHint.hint ? ' · ' + dateHint.hint : '')
      }
    />
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
  dateHint,
  periods,
  setPeriods,
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
            dateHint={dateHint}
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