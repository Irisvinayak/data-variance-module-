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
    display_columns,
    rows = [],
  } = result

  const displayRows    = showAll ? rows : rows.slice(0, 20)
  const periodCount    = comparison_periods.length
  const colSpanWidth   = periodCount * 2

  // All columns to show; columns = only the comparable (numeric, non-code) subset
  const allDisplayCols = display_columns ?? columns
  const comparableSet  = new Set(columns.map((c) => c.toUpperCase()))

  const cellClass = (color) => {
    if (color === 'success') return 'vt-pos'
    if (color === 'danger')  return 'vt-neg'
    return ''
  }

  return (
    <div className="variance-block">

      {/* ── Title + period badges ── */}
      <div className="variance-header">
        <div className="variance-title">📊 {table_name}</div>
        <div className="vt-period-row">
          {comparison_periods.map((p, i) => (
            <span key={i} className="vt-badge vt-badge-prev">
              {periodCount > 1 ? `Prev ${i + 1}` : 'Previous'}: <strong>{p}</strong>
            </span>
          ))}
          <span className="vt-badge vt-badge-curr">
            Current: <strong>{reporting_date}</strong>
          </span>
        </div>
      </div>

      <div className="variance-table-wrapper">
        <table className="variance-table">
          <thead>

            {/* Row 1 — column group labels */}
            <tr>
              <th className="vt-id-th" rowSpan={2}>Identifier</th>
              {allDisplayCols.map((col) =>
                comparableSet.has(col.toUpperCase()) ? (
                  // Comparable (numeric): spans Prev + Current sub-columns
                  <th key={col} className="vt-group-th" colSpan={colSpanWidth}>
                    {col}
                  </th>
                ) : (
                  // Display-only (description / code): single cell across both header rows
                  <th key={col} className="vt-group-th vt-info-th" rowSpan={2}>
                    {col}
                  </th>
                )
              )}
            </tr>

            {/* Row 2 — Prev / Current sub-labels for comparable columns only */}
            <tr>
              {allDisplayCols
                .filter((col) => comparableSet.has(col.toUpperCase()))
                .map((col) =>
                  comparison_periods.map((p, i) => [
                    <th key={`${col}_p${i}`} className="vt-sub vt-sub-prev">
                      {periodCount > 1 ? `Prev ${i + 1}` : 'Previous'}
                      <span className="vt-sub-date">{p}</span>
                    </th>,
                    <th key={`${col}_c${i}`} className="vt-sub vt-sub-curr">
                      Current
                      <span className="vt-sub-date">{reporting_date}</span>
                    </th>,
                  ])
                )}
            </tr>

          </thead>
          <tbody>
            {displayRows.map((row, ri) => (
              <tr key={row.identifier ?? ri} className={ri % 2 === 0 ? '' : 'vt-row-alt'}>

                {/* Identifier cell */}
                <td className="vt-id-td" title={row.identifier}>{row.identifier || '—'}</td>

                {allDisplayCols.map((col) => {
                  const isComparable = comparableSet.has(col.toUpperCase())

                  if (!isComparable) {
                    // Display-only: show current value only, no comparison
                    const val = row.current?.[col]
                    return (
                      <td key={col} className="vt-info-cell">
                        {val != null ? val : '—'}
                      </td>
                    )
                  }

                  // Comparable: [Prev | Current+arrow] per period
                  return comparison_periods.map((_, pi) => {
                    const pKey  = `previous_${pi + 1}`
                    const m     = row.previous?.[pKey]?.[col]
                    const currV = row.current?.[col]
                    const vs    = m?.variance_summary
                    const cc    = cellClass(vs?.color ?? '')

                    return [
                      <td key={`${col}_${pi}_prev`} className="vt-num vt-prev-cell">
                        {m?.value != null ? m.value : '—'}
                      </td>,
                      <td
                        key={`${col}_${pi}_curr`}
                        className={`vt-num vt-curr-cell ${cc}`}
                        title={vs?.text ?? ''}
                      >
                        <span className="vt-curr-val">{currV != null ? currV : '—'}</span>
                        {vs?.arrow && (
                          <span className={`vt-arrow-icon ${cc}`}>
                            {vs.arrow === '▲' ? '↑' : vs.arrow === '▼' ? '↓' : vs.arrow}
                          </span>
                        )}
                        {m?.pct_change?.value && (
                          <span className={`vt-pct-badge ${cc}`}>{m.pct_change.value}</span>
                        )}
                      </td>,
                    ]
                  })
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {rows.length > 20 && (
        <button
          className="btn btn-secondary btn-sm"
          onClick={() => setShowAll((v) => !v)}
        >
          {showAll ? '▲ Show less' : `▼ Show all ${rows.length} rows`}
        </button>
      )}
    </div>
  )
}
