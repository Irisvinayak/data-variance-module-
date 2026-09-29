/**
 * DataVarianceTable — display components for variance results.
 * All styles are self-contained in App.css.
 */

import { useState } from 'react'
import { freqLabel } from './types.js'

// One SVG triangle for both directions, rotated for a decrease. The ↑/↓ text
// glyphs used before come from different font glyphs, so the two rendered at
// visibly different sizes and weights.
function TrendArrow({ arrow }) {
  if (arrow !== '▲' && arrow !== '▼') return arrow || null
  const down = arrow === '▼'
  return (
    <svg
      className="vt-trend-svg"
      viewBox="0 0 10 10"
      width="0.8em"
      height="0.8em"
      aria-label={down ? 'decrease' : 'increase'}
      style={down ? { transform: 'rotate(180deg)' } : undefined}
    >
      <path d="M5 1 L9.5 9 L0.5 9 Z" fill="currentColor" />
    </svg>
  )
}

// Module-level so the default `hiddenCols` prop keeps a stable identity across
// renders — a fresh [] literal in the signature would be a new value every
// time and defeat any memoization a caller adds later.
const EMPTY_COLS = []

// Rows shown before the "Show all" toggle.
const ROW_PREVIEW = 20

const orDash = (v) => (v != null ? v : '—')

const cellClass = (color) => {
  if (color === 'success') return 'vt-pos'
  if (color === 'danger')  return 'vt-neg'
  return ''
}

// Current-side cell of one comparison pair: the value plus its trend arrow,
// % change and delta. Shared by the sequential and vs_current layouts.
function VarianceCell({ value, metric }) {
  const vs = metric?.variance_summary
  const cc = cellClass(vs?.color ?? '')
  return (
    <td className={`vt-num vt-curr-cell ${cc}`} title={vs?.text ?? ''}>
      <div className="vt-curr-wrap">
        <span className="vt-curr-val">{orDash(value)}</span>
        {(vs?.arrow || metric?.pct_change?.value || metric?.change?.value) && (
          <div className="vt-metrics-row">
            {vs?.arrow && (
              <span className={`vt-arrow-icon ${cc}`}>
                <TrendArrow arrow={vs.arrow} />
              </span>
            )}
            {metric?.pct_change?.value && (
              <span className={`vt-pct-badge ${cc}`}>{metric.pct_change.value}</span>
            )}
            <span className={`vt-diff-val ${cc}`}>
              Δ&thinsp;{metric?.change?.value ?? '0'}
            </span>
          </div>
        )}
      </div>
    </td>
  )
}

function ShowAllToggle({ total, showAll, onToggle }) {
  if (total <= ROW_PREVIEW) return null
  return (
    <button className="btn btn-secondary btn-sm" onClick={onToggle}>
      {showAll ? '▲ Show less' : `▼ Show all ${total} rows`}
    </button>
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
// The table name and period badges are not rendered here: TablePanel shows
// them in its panel header via VarianceHeaderMeta.
//
// `hiddenCols` / `onHideCol` are the column-visibility feature. Both default to
// inert values so this component stays usable standalone (and so the feature
// could be removed by deleting the two props at the call site).
export function DataVarianceBlock({
  result,
  hiddenCols = EMPTY_COLS,
  onHideCol,
}) {
  const [showAll, setShowAll] = useState(false)

  if (result?.error) {
    return <div className="error-box">⚠ {result.error}</div>
  }

  const {
    reporting_date,
    comparison_periods = [],
    columns = [],
    display_columns,
    rows = [],
    comparison_mode = 'vs_current',
    chain_dates = [],
  } = result

  const displayRows    = showAll ? rows : rows.slice(0, ROW_PREVIEW)
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
                      return (
                        <td key={col} className="vt-info-cell">
                          {orDash(row.current?.[col])}
                        </td>
                      )
                    }

                    return links.map((lk, li) => {
                      const m = row[lk.key]?.metrics?.[col]
                      // A link's "to" value is the next link's "from" value;
                      // the last link ends at the current period.
                      const toValue = li === links.length - 1
                        ? row.current?.[col]
                        : row[`link_${li + 2}`]?.metrics?.[col]?.value

                      return [
                        <td key={`${col}_${li}_from`} className="vt-num vt-prev-cell">
                          {orDash(m?.value)}
                        </td>,
                        <VarianceCell key={`${col}_${li}_to`} value={toValue} metric={m} />,
                      ]
                    })
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <ShowAllToggle total={rows.length} showAll={showAll} onToggle={() => setShowAll((v) => !v)} />
      </div>
    )
  }

  // ── vs_current mode (default / existing rendering) ────────────────────────
  return (
    <div className="variance-block">

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
                    return (
                      <td key={col} className="vt-info-cell">
                        {orDash(row.current?.[col])}
                      </td>
                    )
                  }

                  // Comparable: [Prev | Current+arrow] per period
                  return comparison_periods.map((_, pi) => {
                    const m = row.previous?.[`previous_${pi + 1}`]?.[col]
                    return [
                      <td key={`${col}_${pi}_prev`} className="vt-num vt-prev-cell">
                        {orDash(m?.value)}
                      </td>,
                      <VarianceCell key={`${col}_${pi}_curr`} value={row.current?.[col]} metric={m} />,
                    ]
                  })
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ShowAllToggle total={rows.length} showAll={showAll} onToggle={() => setShowAll((v) => !v)} />
    </div>
  )
}
