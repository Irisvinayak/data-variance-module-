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

export default function VisualizationPanel({ result, vizState, vizOpen, onExpand, onMinimize }) {
  const [selectedCol, setSelectedCol] = useState(null)
  const [chartType,   setChartType]   = useState('bar')
  const [rowLimit,    setRowLimit]    = useState(15)

  const columns            = result?.columns ?? []
  const rows               = result?.rows ?? []
  const comparison_periods = result?.comparison_periods ?? []
  const reporting_date     = result?.reporting_date ?? 'Current'

  const activeCol = selectedCol ?? columns[0] ?? null

  const chartData = useMemo(() => {
    if (!activeCol || !rows.length) return []
    return rows.slice(0, rowLimit).map((row) => {
      const entry = { name: row.display_label ?? row.identifier ?? '—', Current: null }
      const currRaw = row.current?.[activeCol]
      entry['Current'] = currRaw != null ? Number(currRaw) : null
      comparison_periods.forEach((p, i) => {
        const key = `previous_${i + 1}`
        const raw = row.previous?.[key]?.[activeCol]?.value
        entry[`Prev ${i + 1}`] = raw != null ? Number(raw) : null
      })
      return entry
    })
  }, [activeCol, rows, comparison_periods, rowLimit])

  const isExpanded  = vizState === 'expanded'
  const isMinimized = vizState === 'minimized' || !vizOpen

  const seriesKeys = [
    ...comparison_periods.map((_, i) => `Prev ${i + 1}`),
    'Current',
  ]
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
              <div className="viz-placeholder-sub">No numeric columns available</div>
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
                    <BarChart data={chartData} margin={{ top: 6, right: 16, bottom: 60, left: 10 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(48,54,61,0.8)" />
                      <XAxis
                        dataKey="name"
                        tick={{ fill: '#8b949e', fontSize: 10 }}
                        angle={-40}
                        textAnchor="end"
                        interval={0}
                      />
                      <YAxis
                        tick={{ fill: '#8b949e', fontSize: 10 }}
                        tickFormatter={fmt}
                        width={64}
                      />
                      <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
                      <Legend
                        wrapperStyle={{ fontSize: '0.72rem', color: '#8b949e', paddingTop: 8 }}
                      />
                      {seriesKeys.map((key, i) => (
                        <Bar key={key} dataKey={key} fill={seriesColors[i]} radius={[3, 3, 0, 0]} maxBarSize={28} />
                      ))}
                    </BarChart>
                  ) : (
                    <LineChart data={chartData} margin={{ top: 6, right: 16, bottom: 60, left: 10 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(48,54,61,0.8)" />
                      <XAxis
                        dataKey="name"
                        tick={{ fill: '#8b949e', fontSize: 10 }}
                        angle={-40}
                        textAnchor="end"
                        interval={0}
                      />
                      <YAxis
                        tick={{ fill: '#8b949e', fontSize: 10 }}
                        tickFormatter={fmt}
                        width={64}
                      />
                      <Tooltip content={<CustomTooltip />} />
                      <Legend
                        wrapperStyle={{ fontSize: '0.72rem', color: '#8b949e', paddingTop: 8 }}
                      />
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
