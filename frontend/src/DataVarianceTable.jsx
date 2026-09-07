/**
 * DataVarianceTable — display components for variance results.
 * All styles are self-contained in App.css (no chatbot CSS required).
 */

import { useState } from 'react'
import { freqLabel } from './types.js'

// Module-level so the default `hiddenCols` prop keeps a stable identity across
// renders — a fresh [] literal in the signature would be a new value every
// time and defeat any memoization a caller adds later.
const EMPTY_COLS = []

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

// ── VarianceHeaderMeta ────────────────────────────────────────────────────────
// The table name + period badges, extracted so TablePanel can render them
// INSIDE the panel header row instead of on a second line below it. The panel
// header used to read a static "TABLE OUTPUT" — a label that says nothing the
// surrounding UI doesn't already make obvious — while the only identifying
// information (which table, which periods) sat on its own row underneath,
// costing a full row of vertical space in a panel whose whole job is showing
// as many data rows as possible.
//
// Lives here, next to DataVarianceBlock, because it reads the same result
// shape and has the same sequential/vs_current split — keeping the two in one
// file is what stops the badges drifting from the table they describe.
export function VarianceHeaderMeta({ result }) {
  if (!result || result.error) return null

  const {
    table_name,
    reporting_date,
    comparison_periods = [],
    comparison_mode = 'vs_current',
    chain_dates = [],
  } = result

  const isSequential = comparison_mode === 'sequential' && chain_dates.length >= 2

  return (
    <div className="variance-head-meta">
      <span className="variance-head-name">{table_name}</span>
      <span className="vt-period-row">
        {isSequential ? (
          chain_dates.slice(0, -1).map((fromDate, i) => (
            <span key={i} className="vt-badge vt-badge-prev">
              {fromDate} → <strong>{chain_dates[i + 1]}</strong>
            </span>
          ))
        ) : (
          <>
            {comparison_periods.map((p, i) => (
              <span key={i} className="vt-badge vt-badge-prev">
                <strong>{p}</strong>
              </span>
            ))}
            <span className="vt-badge vt-badge-curr">
              <strong>{reporting_date}</strong>
            </span>
          </>
        )}
      </span>
    </div>
  )
}

// ── DataVarianceBlock ─────────────────────────────────────────────────────────
// `showHeader={false}` when the caller has already rendered VarianceHeaderMeta
// somewhere else (TablePanel puts it in the panel header) — without it the
// name and badges would appear twice.
//
// `hiddenCols` / `onHideCol` are the column-visibility feature. Both default to
// inert values so this component stays usable standalone (and so the feature
// could be removed by deleting the two props at the call site).
export function DataVarianceBlock({
  result,
  showHeader = true,
  hiddenCols = EMPTY_COLS,
  onHideCol,
}) {
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
    comparison_mode = 'vs_current',
    chain_dates = [],
  } = result

  const displayRows    = showAll ? rows : rows.slice(0, 20)
  const periodCount    = comparison_periods.length
  const colSpanWidth   = periodCount * 2

  // All columns to show; columns = only the comparable (numeric, non-code) subset.
  //
  // This filter is the ONLY place column visibility is applied. Every render
  // site below — both header rows and every body row, in both the sequential
  // and vs_current branches — iterates allDisplayCols, so filtering it once
  // keeps them consistent by construction. Safe to do here because no
  // colSpan/rowSpan depends on the column COUNT: colSpanWidth and seqColSpan
  // are derived from the number of periods/links, not columns.
  const hiddenSet      = new Set(hiddenCols.map((c) => c.toUpperCase()))
  const allDisplayCols = (display_columns ?? columns).filter(
    (c) => !hiddenSet.has(c.toUpperCase())
  )
  // Deliberately NOT filtered: comparableSet is only ever a membership
  // predicate applied to entries already taken from allDisplayCols, so
  // narrowing it would be redundant — and if the two ever drifted it would
  // misclassify a *visible* numeric column as an info column.
  const comparableSet  = new Set(columns.map((c) => c.toUpperCase()))

  // Hiding every column is allowed (see ColumnVisibilityMenu's "Hide all"), so
  // this state is reachable. One guard serves both render branches. The menu
  // lives in the panel header, outside this component, so it stays reachable
  // to undo.
  if (allDisplayCols.length === 0) {
    return (
      <div className="variance-block">
        <div className="vt-all-hidden">
          All columns are hidden — use the <strong>Columns</strong> menu above to
          show them again.
        </div>
      </div>
    )
  }

  // Rendered inside each column's group header; absent unless the caller
  // supplies a handler.
  const hideBtn = (col) =>
    onHideCol ? (
      <button
        type="button"
        className="vt-col-hide"
        title={`Hide ${col}`}
        aria-label={`Hide column ${col}`}
        onClick={() => onHideCol(col)}
      >
        ×
      </button>
    ) : null

  const cellClass = (color) => {
    if (color === 'success') return 'vt-pos'
    if (color === 'danger')  return 'vt-neg'
    return ''
  }

  // ── Sequential mode rendering ─────────────────────────────────────────────
  if (comparison_mode === 'sequential' && chain_dates.length >= 2) {
    const links = chain_dates.slice(0, -1).map((fromDate, i) => ({
      fromDate,
      toDate: chain_dates[i + 1],
      key: `link_${i + 1}`,
    }))
    const seqColSpan = links.length * 2

    return (
      <div className="variance-block">

        {/* ── Title + chain badges ── */}
        {showHeader && (
          <div className="variance-header">
            <div className="variance-title">📊 {table_name}</div>
            <div className="vt-period-row">
              {links.map((lk, i) => (
                <span key={i} className="vt-badge vt-badge-prev">
                  {lk.fromDate} → <strong>{lk.toDate}</strong>
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="variance-table-wrapper">
          <table className="variance-table">
            <thead>

              {/* Row 1 — column group labels */}
              <tr>
                <th className="vt-id-th" rowSpan={2}>Identifier</th>
                {allDisplayCols.map((col) =>
                  comparableSet.has(col.toUpperCase()) ? (
                    <th key={col} className="vt-group-th" colSpan={seqColSpan}>
                      {col}
                      {hideBtn(col)}
                    </th>
                  ) : (
                    <th key={col} className="vt-group-th vt-info-th" rowSpan={2}>
                      {col}
                      {hideBtn(col)}
                    </th>
                  )
                )}
              </tr>

              {/* Row 2 — per-link sub-labels */}
              <tr>
                {allDisplayCols
                  .filter((col) => comparableSet.has(col.toUpperCase()))
                  .map((col) =>
                    links.map((lk, i) => [
                      <th key={`${col}_lk${i}_from`} className="vt-sub vt-sub-prev">
                        {lk.fromDate}
                      </th>,
                      <th key={`${col}_lk${i}_to`} className="vt-sub vt-sub-curr">
                        {lk.toDate}
                      </th>,
                    ])
                  )}
              </tr>

            </thead>
            <tbody>
              {displayRows.map((row, ri) => (
                <tr key={row.identifier ?? ri} className={ri % 2 === 0 ? '' : 'vt-row-alt'}>

                  {/* Identifier cell — show display_label, title shows code */}
                  <td className="vt-id-td" title={row.identifier}>
                    {row.display_label ?? row.identifier ?? '—'}
                  </td>

                  {allDisplayCols.map((col) => {
                    const isComparable = comparableSet.has(col.toUpperCase())

                    if (!isComparable) {
                      const val = row.current?.[col]
                      return (
                        <td key={col} className="vt-info-cell">
                          {val != null ? val : '—'}
                        </td>
                      )
                    }

                    return links.map((lk, li) => {
                      const linkData = row[lk.key]
                      const m   = linkData?.metrics?.[col]
                      const vs  = m?.variance_summary
                      const cc  = cellClass(vs?.color ?? '')

                      return [
                        <td key={`${col}_${li}_from`} className="vt-num vt-prev-cell">
                          {m?.value != null ? m.value : '—'}
                        </td>,
                        <td
                          key={`${col}_${li}_to`}
                          className={`vt-num vt-curr-cell ${cc}`}
                          title={vs?.text ?? ''}
                        >
                          <div className="vt-curr-wrap">
                            <span className="vt-curr-val">
                              {li === links.length - 1
                                ? (row.current?.[col] != null ? row.current[col] : '—')
                                : (row[`link_${li + 2}`]?.metrics?.[col]?.value != null
                                    ? row[`link_${li + 2}`].metrics[col].value
                                    : '—')}
                            </span>
                            {(vs?.arrow || m?.pct_change?.value || m?.change?.value) && (
                              <div className="vt-metrics-row">
                                {vs?.arrow && (
                                  <span className={`vt-arrow-icon ${cc}`}>
                                    {vs.arrow === '▲' ? '↑' : vs.arrow === '▼' ? '↓' : vs.arrow}
                                  </span>
                                )}
                                {m?.pct_change?.value && (
                                  <span className={`vt-pct-badge ${cc}`}>{m.pct_change.value}</span>
                                )}
                                <span className={`vt-diff-val ${cc}`}>
                                  Δ&thinsp;{m?.change?.value ?? '0'}
                                </span>
                              </div>
                            )}
                          </div>
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

  // ── vs_current mode (default / existing rendering) ────────────────────────
  return (
    <div className="variance-block">

      {/* ── Title + period badges ── */}
      {showHeader && (
        <div className="variance-header">
          <div className="variance-title">📊 {table_name}</div>
          <div className="vt-period-row">
            {comparison_periods.map((p, i) => (
              <span key={i} className="vt-badge vt-badge-prev">
                <strong>{p}</strong>
              </span>
            ))}
            <span className="vt-badge vt-badge-curr">
              <strong>{reporting_date}</strong>
            </span>
          </div>
        </div>
      )}

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
                    {hideBtn(col)}
                  </th>
                ) : (
                  // Display-only (description / code): single cell across both header rows
                  <th key={col} className="vt-group-th vt-info-th" rowSpan={2}>
                    {col}
                    {hideBtn(col)}
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
                      {p}
                    </th>,
                    <th key={`${col}_c${i}`} className="vt-sub vt-sub-curr">
                      {reporting_date}
                    </th>,
                  ])
                )}
            </tr>

          </thead>
          <tbody>
            {displayRows.map((row, ri) => (
              <tr key={row.identifier ?? ri} className={ri % 2 === 0 ? '' : 'vt-row-alt'}>

                {/* Identifier cell — show display_label, title shows code */}
                <td className="vt-id-td" title={row.identifier}>
                  {row.display_label ?? row.identifier ?? '—'}
                </td>

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
                        <div className="vt-curr-wrap">
                          <span className="vt-curr-val">{currV != null ? currV : '—'}</span>
                          {(vs?.arrow || m?.pct_change?.value || m?.change?.value) && (
                            <div className="vt-metrics-row">
                              {vs?.arrow && (
                                <span className={`vt-arrow-icon ${cc}`}>
                                  {vs.arrow === '▲' ? '↑' : vs.arrow === '▼' ? '↓' : vs.arrow}
                                </span>
                              )}
                              {m?.pct_change?.value && (
                                <span className={`vt-pct-badge ${cc}`}>{m.pct_change.value}</span>
                              )}
                              <span className={`vt-diff-val ${cc}`}>
                                Δ&thinsp;{m?.change?.value ?? '0'}
                              </span>
                            </div>
                          )}
                        </div>
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
