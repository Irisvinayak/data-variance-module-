// NlpInsights.jsx — the written answer to a natural-language question.
//
// The backend (backend/nlp/analysis.py, series.py, conversation.py) already
// returns, next to the table rows: a one-line `summary`, the `applied_filters`
// it used, `analysis_notes` (every assumption it made, e.g. what "significant"
// meant), a management `summary_detail`, a `drill_down` for one element, a
// `series` for trends, and `provenance` (which dates and rows the numbers came
// from). Until now none of that was shown. Everything here is read straight
// from the response — no number is computed in the browser except the CSV
// export, which only re-formats the rows already displayed.

import { useState } from 'react'
import { PropTypes } from '../propTypes.js'

function csvCell(v) {
  const s = v === null || v === undefined ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

// One line per (element, column): current, previous, absolute and % change.
export function resultToCsv(result) {
  const cols = result.columns || []
  const lines = [['Element', 'Identifier', 'Column', 'Current', 'Previous', 'Change', '% Change', 'Status']]
  for (const row of result.rows || []) {
    const prev = row.previous?.previous_1 || {}
    const cells = row.analysis?.cells || {}
    for (const col of cols) {
      const m = prev[col]
      if (!m) continue
      const cell = cells[col] || {}
      lines.push([
        row.display_label ?? row.identifier, row.identifier, col,
        row.current?.[col.toUpperCase()], m.value,
        cell.change ?? m.change, cell.pct_change ?? m.pct_change, cell.status ?? '',
      ])
    }
  }
  const header = []
  if (result.summary) header.push([`# ${result.summary}`])
  for (const f of result.applied_filters || []) header.push([`# Filter: ${f}`])
  if (result.provenance) {
    const p = result.provenance
    header.push([`# Source: ${p.return_name || p.return_id} / ${p.table}; ${p.current_date} vs ${(p.previous_dates || []).join(', ')}`])
  }
  return [...header, ...lines].map((r) => r.map(csvCell).join(',')).join('\n')
}

function downloadCsv(result) {
  const blob = new Blob([resultToCsv(result)], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `variation_${result.provenance?.current_date || 'result'}.csv`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export default function NlpInsights({ result, onNewConversation }) {
  const [showSource, setShowSource] = useState(false)
  if (!result) return null
  const {
    summary, applied_filters: filters, analysis_notes: notes, summary_detail: detail,
    drill_down: drill, series, provenance, formula, interpretation,
  } = result
  const followupOf = interpretation?.followup_of

  return (
    <div className="nlp-insights" aria-live="polite">
      <div className="nlp-insights-head">
        {summary && <div className="nlp-insights-summary">{summary}</div>}
        <div className="nlp-insights-actions">
          {(result.rows || []).length > 0 && (
            <button type="button" className="nlp-insights-btn" onClick={() => downloadCsv(result)}
              title="Download the rows shown as CSV (opens in Excel)">
              Export CSV
            </button>
          )}
          {provenance && (
            <button type="button" className="nlp-insights-btn" onClick={() => setShowSource((s) => !s)}>
              {showSource ? 'Hide source' : 'Source'}
            </button>
          )}
          {result.conversation_state && (
            <button type="button" className="nlp-insights-btn" onClick={onNewConversation}
              title="Your next question will start over instead of refining this answer">
              New question
            </button>
          )}
        </div>
      </div>

      {followupOf && (
        <div className="nlp-insights-line nlp-insights-muted">Refining the previous answer: “{followupOf}”</div>
      )}
      {filters?.length > 0 && (
        <div className="nlp-insights-line">
          {filters.map((f) => <span key={f} className="nlp-interpretation-chip"><span className="nlp-interpretation-chip-value">{f}</span></span>)}
        </div>
      )}
      {notes?.map((n, i) => (
        <div key={`${i}:${n}`} className="nlp-insights-line nlp-insights-note">{n}</div>
      ))}
      {formula && !notes?.includes(formula) && <div className="nlp-insights-line nlp-insights-note">{formula}</div>}

      {detail && (
        <div className="nlp-insights-block">
          <div>{detail.headline}</div>
          <div className="nlp-insights-cols">
            <div>
              <div className="nlp-insights-subhead">Largest increases</div>
              {detail.top_increases.length ? detail.top_increases.map((t) => <div key={t}>{t}</div>) : <div>None</div>}
            </div>
            <div>
              <div className="nlp-insights-subhead">Largest decreases</div>
              {detail.top_decreases.length ? detail.top_decreases.map((t) => <div key={t}>{t}</div>) : <div>None</div>}
            </div>
          </div>
        </div>
      )}

      {drill && (
        <div className="nlp-insights-block">
          <div className="nlp-insights-subhead">Details: {drill.label}</div>
          <table className="nlp-insights-table">
            <thead><tr><th>Column</th><th>Current</th><th>Previous</th><th>Change</th><th>% Change</th></tr></thead>
            <tbody>
              {drill.columns.map((c) => (
                <tr key={c.column}>
                  <td>{c.column}</td><td>{c.current ?? '—'}</td><td>{c.previous ?? '—'}</td>
                  <td>{c.change ?? '—'}</td><td>{c.pct_change != null ? `${c.pct_change}%` : 'N/A'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="nlp-insights-muted">{drill.note}</div>
        </div>
      )}

      {series?.items?.length > 0 && (
        <div className="nlp-insights-block nlp-insights-scroll">
          <table className="nlp-insights-table">
            <thead>
              <tr><th>Element</th><th>Column</th>{series.dates.map((d) => <th key={d}>{d}</th>)}<th>Trend</th></tr>
            </thead>
            <tbody>
              {series.items.slice(0, 50).map((it) => (
                <tr key={`${it.identifier}:${it.column}`}>
                  <td>{it.label}</td><td>{it.column}</td>
                  {it.values.map((v, i) => (
                    <td key={i} className={it.unusual_steps?.includes(i) ? 'nlp-insights-flag' : ''}>{v ?? '—'}</td>
                  ))}
                  <td>{it.trend}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showSource && provenance && (
        <div className="nlp-insights-block nlp-insights-muted">
          <div>Return: {provenance.return_name || provenance.return_id} — table {provenance.table}</div>
          <div>Current period: {provenance.current_date}; compared with: {(provenance.previous_dates || []).join(', ') || 'none'}</div>
          <div>
            Rows compared: {provenance.rows_compared}; shown: {provenance.rows_in_result}
            {provenance.removed_rows ? `; rows no longer reported: ${provenance.removed_rows}` : ''}
            {provenance.duplicate_keys ? `; duplicate keys: ${provenance.duplicate_keys}` : ''}
            {provenance.truncated ? '; result truncated at the row limit' : ''}
          </div>
          <div>{provenance.formula}</div>
        </div>
      )}
    </div>
  )
}

NlpInsights.propTypes = {
  result: PropTypes.object,
  onNewConversation: PropTypes.func,
}
