/**
 * VisualizationPanel — side-by-side chart panel.
 * Renders bar or line chart for the variance result using recharts.
 */
import { useState, useMemo } from 'react'
import {
  ResponsiveContainer,
  BarChart, Bar,
  LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from 'recharts'
import PanelHeader from './PanelHeader.jsx'

const CURR_COLOR  = '#58a6ff'
const PREV_COLORS = ['#8b949e', '#3fb950', '#e3b341', '#f85149']

const fmt = (v) => {
  if (v == null) return '—'
  const n = Number(v)
  if (isNaN(n)) return v
  return Math.abs(n) >= 1_000_000
    ? (n / 1_000_000).toFixed(2) + 'M'
    : Math.abs(n) >= 1_000
    ? (n / 1_000).toFixed(1) + 'K'
    : n.toLocaleString()
}

// ── Shared axis/legend config ────────────────────────────────────────────────
// Defined once and spread into both the Bar and the Line chart: the two had
// identical copies of every axis prop, which is how the X-axis and the legend
// ended up needing the same fix twice.

// Row labels here are full sentences from the returns themselves ("Total
// Number of Staff", "(iii) Clerical Staff"), rendered at -40°. A rotated label
// of that length needs far more vertical room than the 60px the bottom margin
// reserved, so it overflowed into the legend sitting in that same margin.
// Two independent fixes, because either alone still breaks on the next
// longer-than-expected label:
//   1. the legend moves OUT of the bottom margin entirely (see CHART_LEGEND),
//      so nothing shares space with the axis labels;
//   2. the labels are truncated to a bounded width, so the space they need
//      stops depending on how verbose one particular return happens to be.
const MAX_TICK_CHARS = 22

const truncateTick = (value) => {
  const text = String(value ?? '')
  return text.length > MAX_TICK_CHARS ? text.slice(0, MAX_TICK_CHARS - 1) + '…' : text
}

const CHART_MARGIN = { top: 6, right: 16, bottom: 72, left: 10 }

const X_AXIS_PROPS = {
  dataKey: 'name',
  tick: { fill: '#8b949e', fontSize: 10 },
  angle: -40,
  textAnchor: 'end',
  interval: 0,
  height: 78,
  tickFormatter: truncateTick,
}

const Y_AXIS_PROPS = {
  tick: { fill: '#8b949e', fontSize: 10 },
  width: 64,
}

// Top-aligned: the bottom of the chart belongs to the rotated axis labels, and
// how much room they need varies with the data, so no fixed bottom margin can
// keep the two apart reliably.
const CHART_LEGEND = {
  verticalAlign: 'top',
  align: 'right',
  height: 24,
  wrapperStyle: { fontSize: '0.72rem', color: '#8b949e', paddingBottom: 6 },
}

// `label` is the RAW row name — tickFormatter only shortens the axis, never
// the data — so hovering still shows the full, untruncated label.
const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="viz-tooltip">
      <div className="viz-tooltip-label">{label}</div>
      {payload.map((p) => (
        <div key={p.name} className="viz-tooltip-row">
          <span className="viz-tooltip-dot" style={{ background: p.fill || p.color }} />
          <span className="viz-tooltip-name">{p.name}:</span>
          <span className="viz-tooltip-val">{fmt(p.value)}</span>
        </div>
      ))}
    </div>
  )
}

export default function VisualizationPanel({
  result, vizState, vizOpen, onExpand, onMinimize, hiddenCols = [],
}) {
  const [selectedCol, setSelectedCol] = useState(null)
  const [chartType,   setChartType]   = useState('bar')
  const [rowLimit,    setRowLimit]    = useState(15)

  // Columns hidden in the table are hidden here too. result.columns is the
  // comparable/numeric subset, so hidden INFO columns simply never intersect
  // it — no special case needed for them.
  const hiddenSet          = new Set(hiddenCols.map((c) => c.toUpperCase()))
  const columns            = (result?.columns ?? []).filter((c) => !hiddenSet.has(c.toUpperCase()))
  const rows               = result?.rows ?? []
  const comparison_periods = result?.comparison_periods ?? []
  const reporting_date     = result?.reporting_date ?? 'Current'

  // Validated against `columns` rather than trusted outright. selectedCol is
  // sticky local state, so without this it can point at a column that is now
  // hidden — or (a pre-existing bug) at a column from a previously computed
  // table, which made <select value=...> render blank because the value had no
  // matching <option>. Derived rather than reset in an effect: no extra render,
  // and nothing to keep in sync.
  const activeCol = selectedCol && columns.includes(selectedCol) ? selectedCol : (columns[0] ?? null)

  // Series are keyed by the REPORTING DATE, not "Current"/"Prev 1". Recharts
  // uses the data key verbatim as the series name in both the legend and the
  // tooltip, so keying by date is what makes them read "31-DEC-2024" instead
  // of a position label the user then has to map back to a date themselves —
  // especially once dates are hand-picked via the Date checkboxes and are no
  // longer a predictable calendar walk backwards.
  //
  // comparison_periods[i] is the date behind previous_{i+1} (both come from
  // calculate_variance's prev_dates, in the same order), so the pairing here
  // is exact rather than positional guesswork.
  const chartData = useMemo(() => {
    if (!activeCol || !rows.length) return []
    return rows.slice(0, rowLimit).map((row) => {
      const entry = { name: row.display_label ?? row.identifier ?? '—' }
      const currRaw = row.current?.[activeCol]
      entry[reporting_date] = currRaw != null ? Number(currRaw) : null
      comparison_periods.forEach((p, i) => {
        const raw = row.previous?.[`previous_${i + 1}`]?.[activeCol]?.value
        entry[p] = raw != null ? Number(raw) : null
      })
      return entry
    })
  }, [activeCol, rows, comparison_periods, reporting_date, rowLimit])

  const isExpanded  = vizState === 'expanded'
  const isMinimized = vizState === 'minimized' || !vizOpen

  // Exactly the order the previous `Prev 1..N, Current` keys had — the current
  // period stays last so it keeps CURR_COLOR, and comparison_periods[i] keeps
  // PREV_COLORS[i]. Only the NAMES changed, never the series order or colors.
  const seriesKeys = [...comparison_periods, reporting_date]
  const seriesColors = [
    ...comparison_periods.map((_, i) => PREV_COLORS[i] ?? PREV_COLORS[0]),
    CURR_COLOR,
  ]

  return (
    <div className={`bottom-panel-shell${isMinimized ? ' panel-shell-collapsed' : ''}`}>
      <PanelHeader
        title="Visualization"
        icon="📈"
        onExpand={onExpand}
        onMinimize={onMinimize}
        isExpanded={isExpanded}
        isMinimized={isMinimized}
      />

      {isMinimized ? (
        <div className="panel-collapsed-body">
          <span className="panel-collapsed-label">📈 Visualization</span>
        </div>
      ) : (
        <div className="bottom-panel-body viz-panel-body">

          {!result ? (
            <div className="viz-placeholder">
              <div className="viz-placeholder-icon">📈</div>
              <div className="viz-placeholder-title">Visualization</div>
              <div className="viz-placeholder-sub">Run a query to see charts here</div>
            </div>
          ) : !activeCol ? (
            <div className="viz-placeholder">
              {/* Distinguishes "this table has nothing chartable" from "you
                  hid everything chartable" — otherwise hiding the last value
                  column looks like a broken chart. */}
              <div className="viz-placeholder-sub">
                {hiddenSet.size > 0 && (result?.columns ?? []).length > 0
                  ? 'Every value column is hidden — show one from the Columns menu.'
                  : 'No numeric columns available'}
              </div>
            </div>
          ) : (
            <div className="viz-content">

              {/* ── Controls ─────────────────────────────────────────── */}
              <div className="viz-controls">
                <div className="viz-ctrl-group">
                  <span className="viz-ctrl-label">Column</span>
                  <select
                    className="viz-select"
                    value={activeCol}
                    onChange={(e) => setSelectedCol(e.target.value)}
                  >
                    {columns.map((c) => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </select>
                </div>

                <div className="viz-ctrl-group">
                  <span className="viz-ctrl-label">Type</span>
                  <div className="viz-type-btns">
                    {['bar', 'line'].map((t) => (
                      <button
                        key={t}
                        className={`viz-type-btn${chartType === t ? ' viz-type-btn-on' : ''}`}
                        onClick={() => setChartType(t)}
                      >
                        {t === 'bar' ? '▬ Bar' : '〜 Line'}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="viz-ctrl-group">
                  <span className="viz-ctrl-label">Rows</span>
                  <div className="viz-type-btns">
                    {[10, 15, 25, 50].map((n) => (
                      <button
                        key={n}
                        className={`viz-type-btn${rowLimit === n ? ' viz-type-btn-on' : ''}`}
                        onClick={() => setRowLimit(n)}
                      >
                        {n}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* ── Chart ────────────────────────────────────────────── */}
              <div className="viz-chart-wrap">
                <ResponsiveContainer width="100%" height="100%">
                  {chartType === 'bar' ? (
                    <BarChart data={chartData} margin={CHART_MARGIN}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(48,54,61,0.8)" />
                      <XAxis {...X_AXIS_PROPS} />
                      <YAxis {...Y_AXIS_PROPS} tickFormatter={fmt} />
                      <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
                      <Legend {...CHART_LEGEND} />
                      {seriesKeys.map((key, i) => (
                        <Bar key={key} dataKey={key} fill={seriesColors[i]} radius={[3, 3, 0, 0]} maxBarSize={28} />
                      ))}
                    </BarChart>
                  ) : (
                    <LineChart data={chartData} margin={CHART_MARGIN}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(48,54,61,0.8)" />
                      <XAxis {...X_AXIS_PROPS} />
                      <YAxis {...Y_AXIS_PROPS} tickFormatter={fmt} />
                      <Tooltip content={<CustomTooltip />} />
                      <Legend {...CHART_LEGEND} />
                      {seriesKeys.map((key, i) => (
                        <Line
                          key={key}
                          type="monotone"
                          dataKey={key}
                          stroke={seriesColors[i]}
                          strokeWidth={2}
                          dot={{ r: 3, fill: seriesColors[i] }}
                          activeDot={{ r: 5 }}
                        />
                      ))}
                    </LineChart>
                  )}
                </ResponsiveContainer>
              </div>

            </div>
          )}

        </div>
      )}
    </div>
  )
}
