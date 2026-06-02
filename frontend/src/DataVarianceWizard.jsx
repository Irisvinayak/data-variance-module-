/**
 * DataVarianceWizard — standalone 5-step wizard.
 * Renders as a full page section.  No chatbot coupling.
 */

import { useState } from 'react'
import { findReturnTables, computeVariance } from './api.js'
import { DataVarianceBlock }                 from './DataVarianceTable.jsx'
import { VARIANCE_STEPS, freqLabel, dateHintForFreq } from './types.js'

const STEP_ORDER = [
  VARIANCE_STEPS.RETURN_NAME,
  VARIANCE_STEPS.TABLE,
  VARIANCE_STEPS.DATE,
  VARIANCE_STEPS.PERIODS,
  VARIANCE_STEPS.RESULT,
]

const STEP_LABELS = {
  [VARIANCE_STEPS.RETURN_NAME]: 'Enter return name',
  [VARIANCE_STEPS.TABLE]:       'Select table',
  [VARIANCE_STEPS.DATE]:        'Enter reporting date',
  [VARIANCE_STEPS.PERIODS]:     'Select periods',
  [VARIANCE_STEPS.RESULT]:      'View result',
}

export default function DataVarianceWizard() {
  const [step,       setStep]       = useState(VARIANCE_STEPS.RETURN_NAME)
  const [returnName, setReturnName] = useState('')
  const [returnInfo, setReturnInfo] = useState(null)
  const [tableName,  setTableName]  = useState('')
  const [dateStr,    setDateStr]    = useState('')
  const [periods,    setPeriods]    = useState(1)
  const [result,     setResult]     = useState(null)
  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState('')

  const stepIndex = STEP_ORDER.indexOf(step)

  const tables = (returnInfo?.tables || []).filter(
    (t, i, arr) =>
      t.table_name && arr.findIndex((x) => x.table_name === t.table_name) === i
  )
  const dateHint = returnInfo ? dateHintForFreq(returnInfo.report_freq) : { example: '', hint: '' }

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleFindReturn = async () => {
    const name = returnName.trim()
    if (!name) return
    setLoading(true)
    setError('')
    try {
      const info = await findReturnTables(name)
      setReturnInfo(info)
      setTableName(info.tables?.[0]?.table_name ?? '')
      setStep(VARIANCE_STEPS.TABLE)
    } catch (err) {
      setError(err.message || 'Failed to find return.')
    } finally {
      setLoading(false)
    }
  }

  const handleCompute = async () => {
    if (!returnInfo || !tableName || !dateStr) return
    setLoading(true)
    setError('')
    try {
      const res = await computeVariance({
        return_id:          returnInfo.return_id,
        table_mapping_path: returnInfo.table_mapping_path,
        table_name:         tableName,
        reporting_date:     dateStr.trim(),
        reporting_period:   periods,
      })
      setResult(res)
      setStep(VARIANCE_STEPS.RESULT)
    } catch (err) {
      setError(err.message || 'Failed to compute variance.')
    } finally {
      setLoading(false)
    }
  }

  const handleReset = () => {
    setStep(VARIANCE_STEPS.RETURN_NAME)
    setReturnName('')
    setReturnInfo(null)
    setTableName('')
    setDateStr('')
    setPeriods(1)
    setResult(null)
    setError('')
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="wizard">
      {/* Progress bar */}
      <div className="wizard-progress">
        {STEP_ORDER.map((s, i) => (
          <div key={s} className="wizard-step-wrap">
            <div className={`wizard-dot ${i < stepIndex ? 'done' : i === stepIndex ? 'active' : ''}`} />
            {i < STEP_ORDER.length - 1 && (
              <div className={`wizard-line ${i < stepIndex ? 'done' : ''}`} />
            )}
          </div>
        ))}
        <span className="wizard-step-label">{STEP_LABELS[step]}</span>
      </div>

      {error && <div className="error-box">⚠ {error}</div>}

      {/* Step 1 — Return name */}
      {step === VARIANCE_STEPS.RETURN_NAME && (
        <div className="wizard-section">
          <div className="field-label">Return / Report Name</div>
          <div className="field-hint">Enter the return code or name (e.g. CIMS_RAQ, BSR1).</div>
          <div className="input-row">
            <input
              className="text-input"
              value={returnName}
              onChange={(e) => setReturnName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !loading && handleFindReturn()}
              placeholder="e.g. CIMS_RAQ"
              autoFocus
            />
            <button
              className="btn"
              disabled={!returnName.trim() || loading}
              onClick={handleFindReturn}
            >
              {loading ? 'Finding…' : 'Find Tables →'}
            </button>
          </div>
          {loading && <div className="loading-row"><span className="spinner" /> Looking up return…</div>}
        </div>
      )}

      {/* Step 2 — Table selection */}
      {step === VARIANCE_STEPS.TABLE && returnInfo && (
        <div className="wizard-section">
          <div className="field-label">Select Table</div>
          <div className="field-hint">
            <strong>{returnInfo.return_name}</strong>
            &nbsp;·&nbsp;{freqLabel(returnInfo.report_freq)}
          </div>
          <div className="table-list">
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
          <div className="row-end">
            <button className="btn btn-secondary" onClick={() => setStep(VARIANCE_STEPS.RETURN_NAME)}>
              ← Back
            </button>
            <button className="btn" disabled={!tableName} onClick={() => setStep(VARIANCE_STEPS.DATE)}>
              Next →
            </button>
          </div>
        </div>
      )}

      {/* Step 3 — Reporting date */}
      {step === VARIANCE_STEPS.DATE && (
        <div className="wizard-section">
          <div className="field-label">Reporting Date</div>
          <div className="field-hint">
            Format: DD-MMM-YYYY &nbsp;·&nbsp; e.g. <strong>{dateHint.example}</strong>
            <br />
            <em>{dateHint.hint}</em>
          </div>
          <div className="input-row">
            <input
              className="text-input"
              value={dateStr}
              onChange={(e) => setDateStr(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && dateStr.trim() && setStep(VARIANCE_STEPS.PERIODS)}
              placeholder={dateHint.example}
              autoFocus
            />
          </div>
          <div className="row-end">
            <button className="btn btn-secondary" onClick={() => setStep(VARIANCE_STEPS.TABLE)}>
              ← Back
            </button>
            <button
              className="btn"
              disabled={!dateStr.trim()}
              onClick={() => setStep(VARIANCE_STEPS.PERIODS)}
            >
              Next →
            </button>
          </div>
        </div>
      )}

      {/* Step 4 — Periods */}
      {step === VARIANCE_STEPS.PERIODS && (
        <div className="wizard-section">
          <div className="field-label">Comparison Periods</div>
          <div className="field-hint">
            How many previous periods to compare against <strong>{dateStr}</strong>?
          </div>
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
          <div className="row-end">
            <button className="btn btn-secondary" onClick={() => setStep(VARIANCE_STEPS.DATE)}>
              ← Back
            </button>
            <button className="btn" disabled={loading} onClick={handleCompute}>
              {loading
                ? <><span className="spinner" /> Computing…</>
                : 'Compute Variance'}
            </button>
          </div>
          {loading && (
            <div className="loading-row"><span className="spinner" /> Querying Oracle and computing variance…</div>
          )}
        </div>
      )}

      {/* Step 5 — Result */}
      {step === VARIANCE_STEPS.RESULT && result && (
        <div className="wizard-section">
          <DataVarianceBlock result={result} />
          <div className="row-end" style={{ marginTop: 16 }}>
            <button className="btn btn-secondary" onClick={handleReset}>
              ↺ New Query
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
