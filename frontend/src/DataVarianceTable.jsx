/**
 * DataVarianceTable — display components for variance results.
 * All styles are self-contained in App.css (no chatbot CSS required).
 */

import { useState } from 'react'
import { freqLabel } from './types.js'

// ── VarianceFindBlock ─────────────────────────────────────────────────────────
export function VarianceFindBlock({ info, onSelect }) {
  const tables = (info?.tables || []).filter(
    (t, i, arr) =>
      t.table_name && arr.findIndex((x) => x.table_name === t.table_name) === i
  )
  const [selected, setSelected] = useState(tables[0]?.table_name ?? '')

  return (
    <div className="vfb-card">
      <div className="vfb-meta">
        <span><strong>Return:</strong> {info.return_name}</span>
        <span><strong>Frequency:</strong> {freqLabel(info.report_freq)}</span>
      </div>
      <p className="vfb-label">Select a table:</p>
      <div className="table-list">
        {tables.map((t) => (
          <div
            key={t.table_name}
            className={`table-item${selected === t.table_name ? ' selected' : ''}`}
            onClick={() => setSelected(t.table_name)}
          >
            {t.table_name}
          </div>
        ))}
      </div>
      <div className="row-end">
        <button
          className="btn"
          disabled={!selected}
          onClick={() => onSelect?.(info, selected)}
        >
          Use this table →
        </button>
      </div>
    </div>
  )
}

// ── DataVarianceBlock ─────────────────────────────────────────────────────────
export function DataVarianceBlock({ result }) {
  const [showAll, setShowAll] = useState(false)

  if (result?.error) {
    return <div className="error-box">⚠ {result.error}</div>
  }

  const {
    table_name,
    reporting_date,
    comparison_periods = [],
    columns = [],
    rows = [],
  } = result

  const displayRows = showAll ? rows : rows.slice(0, 15)

  const colorClass = (colorStr) => {
    if (colorStr === 'success') return 'vt-pos'
    if (colorStr === 'danger')  return 'vt-neg'
    return ''
  }

  return (
    <div className="variance-block">
      <div className="variance-title">📈 Data Variance — {table_name}</div>
      <div className="variance-subtitle">
        Reporting: <strong>{reporting_date}</strong>
        {comparison_periods.length > 0 && (
          <> &nbsp;vs&nbsp; <strong>{comparison_periods.join(', ')}</strong></>
        )}
      </div>

      <div className="variance-table-wrapper">
        <table className="variance-table">
          <thead>
            <tr>
              <th className="vt-concept-col">Identifier</th>
              {columns.map((col) => (
                <th key={col} className="vt-num-col" colSpan={1 + comparison_periods.length}>
                  {col}
                </th>
              ))}
            </tr>
            <tr>
              <th />
              {columns.map((col) =>
                ['Current', ...comparison_periods.map((_, i) => `Prev ${i + 1}`)].map((lbl) => (
                  <th key={col + lbl} className="vt-num-col vt-sub">{lbl}</th>
                ))
              )}
            </tr>
          </thead>
          <tbody>
            {displayRows.map((row, ri) => (
              <tr key={row.identifier ?? ri}>
                <td className="vt-concept">{row.identifier || '—'}</td>
                {columns.map((col) => {
                  const curr = row.current?.[col]
                  return [
                    <td key={col + '_curr'} className="vt-num">{curr ?? '—'}</td>,
                    ...comparison_periods.map((_, pi) => {
                      const pKey = `previous_${pi + 1}`
                      const m    = row.previous?.[pKey]?.[col]
                      const vs   = m?.variance_summary
                      return (
                        <td
                          key={col + pKey}
                          className={`vt-num ${colorClass(vs?.color ?? '')}`}
                          title={vs?.text ?? ''}
                        >
                          {m ? (
                            <>
                              {m.value ?? '—'}
                              {vs?.arrow && <span className="vt-arrow">{vs.arrow}</span>}
                              {m.pct_change?.value && (
                                <span className="vt-pct">{m.pct_change.value}</span>
                              )}
                            </>
                          ) : '—'}
                        </td>
                      )
                    }),
                  ]
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {rows.length > 15 && (
        <button
          className="btn btn-secondary btn-sm"
          onClick={() => setShowAll((v) => !v)}
        >
          {showAll ? 'Show less ▲' : `Show all ${rows.length} rows ▼`}
        </button>
      )}
    </div>
  )
}
