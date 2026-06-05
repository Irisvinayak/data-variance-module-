/**
 * InputPanel — top-left panel. Configures the variance query (table, date, periods, compute).
 */
import { VARIANCE_STEPS, freqLabel } from '../types.js'

export default function InputPanel({
  step, setStep,
  returnInfo, tables, tableName, setTableName,
  dateStr, setDateStr, dateHint,
  periods, setPeriods,
  loading, error,
  handleCompute, handleReset,
}) {
  const canCompute = !!(returnInfo && tableName && dateStr.trim() && !loading)

  return (
    <div className="top-panel-shell">
      <div className="top-panel-header">
        <span className="top-panel-icon">⚙️</span>
        <span>Configure Query</span>
      </div>

      <div className="top-panel-body">
        {error && <div className="error-box" style={{ marginBottom: 12 }}>⚠ {error}</div>}

        {/* No return found yet */}
        {!returnInfo && (
          <div className="ip-empty">
            <div className="ip-empty-icon">🔎</div>
            <div className="ip-empty-text">Search for a return in the panel on the right to begin</div>
          </div>
        )}

        {/* Query configuration — all fields visible once return is found */}
        {returnInfo && step !== VARIANCE_STEPS.RESULT && (
          <div className="ip-form">
            {/* Return info line */}
            <div className="ip-return-info">
              <span className="ip-return-label">Return</span>
              <span className="ip-return-name">{returnInfo.return_name}</span>
              <span className="vt-badge vt-badge-prev">{freqLabel(returnInfo.report_freq)}</span>
            </div>

            {/* Table */}
            <div className="ip-field">
              <div className="field-label">Table</div>
              <div className="table-list ip-table-list">
                {tables.map((t) => (
                  <div
                    key={t.table_name}
                    className={`table-item${tableName === t.table_name ? ' selected' : ''}`}
                    onClick={() => setTableName(t.table_name)}
                  >
                    {t.table_name}
                  </div>
                ))}
              </div>
            </div>

            {/* Date */}
            <div className="ip-field">
              <div className="field-label">Reporting Date</div>
              <div className="field-hint">
                Format: DD-MMM-YYYY &nbsp;·&nbsp; e.g. <strong>{dateHint.example}</strong>
              </div>
              <input
                className="text-input"
                value={dateStr}
                onChange={(e) => setDateStr(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && canCompute && handleCompute()}
                placeholder={dateHint.example || 'DD-MMM-YYYY'}
              />
            </div>

            {/* Periods */}
            <div className="ip-field">
              <div className="field-label">Comparison Periods</div>
              <div className="period-chips">
                {[1, 2, 3].map((n) => (
                  <button
                    key={n}
                    className={`period-chip${periods === n ? ' selected' : ''}`}
                    onClick={() => setPeriods(n)}
                  >
                    {n} {n === 1 ? 'period' : 'periods'}
                  </button>
                ))}
              </div>
            </div>

            {/* Compute */}
            <div className="ip-actions">
              <button className="btn" disabled={!canCompute} onClick={handleCompute}>
                {loading
                  ? <><span className="spinner" /> Computing…</>
                  : 'Compute Variance'}
              </button>
            </div>

            {loading && (
              <div className="loading-row">
                <span className="spinner" /> Querying Oracle and computing variance…
              </div>
            )}
          </div>
        )}

        {/* Result state */}
        {returnInfo && step === VARIANCE_STEPS.RESULT && (
          <div className="ip-success">
            <div className="ip-success-icon">✅</div>
            <div className="ip-success-text">Variance computed successfully</div>
            <div className="ip-success-sub">Results are shown in the table panel below</div>
            <div className="ip-actions" style={{ justifyContent: 'center', marginTop: 16 }}>
              <button className="btn btn-secondary" onClick={handleReset}>
                ↺ New Query
              </button>
              <button className="btn" disabled={loading} onClick={handleCompute}>
                {loading ? <><span className="spinner" /> Recomputing…</> : '↻ Recompute'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
