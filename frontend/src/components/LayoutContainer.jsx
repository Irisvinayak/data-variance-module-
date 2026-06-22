/**
 * LayoutContainer — main layout orchestrator.
 *
 * Authorization flow:
 *   1. On mount → calls GET /auth/my-returns?loginId=...
 *      → stores allowedFormIds as a Set (e.g. Set{"2001","2007","4016",...})
 *
 *   2. On search → calls GET /variance/find?return_name=...&loginId=...
 *      → filters the results against allowedFormIds BEFORE showing to user:
 *          • Single result  → shown only if return_id is in allowedFormIds
 *          • Candidates list → filtered to only allowed return_ids
 *          • If nothing passes filter → shows "no access" error
 *
 * Layout:
 *   TOP    : ControlBar — compact two-row toolbar, always visible
 *   BOTTOM : Analysis area — hidden until first compute succeeds.
 */
import { useState, useRef, useEffect } from 'react'
import { Panel, Group, Separator } from 'react-resizable-panels'

import { findReturnTables, computeVariance, getMyReturns } from '../api.js'
import { VARIANCE_STEPS, COMPARISON_MODES, dateHintForFreq } from '../types.js'

import ControlBar from './ControlBar.jsx'
import TablePanel from './TablePanel.jsx'
import VisualizationPanel from './VisualizationPanel.jsx'

export default function LayoutContainer({ loginId = '', uid = '' }) {

  // ─── Allowed form IDs for this user ──────────────────────────────────────
  // Populated once on mount from GET /auth/my-returns
  // e.g. Set { "2001", "2007", "4016", "6001", ... }
  const [allowedFormIds, setAllowedFormIds] = useState(null)   // null = not loaded yet
  const [allowedFormNames, setAllowedFormNames] = useState(null)   // null = not loaded yet
  const [authLoading, setAuthLoading] = useState(true)
  const [authError, setAuthError] = useState('')

  // ─── NLP bar state ───────────────────────────────────────────────────────
  const [nlpQuery, setNlpQuery] = useState('')

  // ─── Wizard state ────────────────────────────────────────────────────────
  const [step, setStep] = useState(VARIANCE_STEPS.RETURN_NAME)
  const [returnName, setReturnName] = useState('')
  const [returnInfo, setReturnInfo] = useState(null)
  const [candidates, setCandidates] = useState(null)
  const [tableName, setTableName] = useState('')
  const [dateStr, setDateStr] = useState('')
  const [periods, setPeriods] = useState(1)
  const [comparisonMode, setComparisonMode] = useState(COMPARISON_MODES.VS_CURRENT)
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // ─── Panel state ─────────────────────────────────────────────────────────
  const tablePanelRef = useRef(null)
  const vizPanelRef = useRef(null)
  const [savedTablePct, setSavedTablePct] = useState(70)
  const [tableState, setTableState] = useState('normal')
  const [vizState, setVizState] = useState('normal')
  const [vizOpen, setVizOpen] = useState(false)

  // ─── Derived ─────────────────────────────────────────────────────────────
  const tables = (returnInfo?.tables || []).filter(
    (t, i, arr) => t.table_name && arr.findIndex((x) => x.table_name === t.table_name) === i
  )
  const dateHint = returnInfo ? dateHintForFreq(returnInfo.report_freq) : { example: '', hint: '' }

  // ─── Step 1: Fetch allowed form IDs on mount ─────────────────────────────
  useEffect(() => {
    if (!loginId) {
      setAuthLoading(false)
      setAuthError('No loginId found. Contact your administrator.')
      return
    }

    setAuthLoading(true)
    getMyReturns(loginId)
      .then((data) => {
        // data.allowed_forms = ["2001", "2007", "4016", ...]
        setAllowedFormIds(new Set(data.allowed_forms || []))
        setAuthLoading(false)
      })
      .catch((err) => {
        setAuthError(err.message || 'Failed to load user permissions.')
        setAuthLoading(false)
      })
  }, [loginId])

  // ─── Filter helper — check if a return_id is allowed ─────────────────────
  const isAllowed = (returnId) => {
    if (!allowedFormIds) return false
    return allowedFormIds.has(String(returnId))
  }

  // ─── Step 2: Filter search results against allowedFormIds ─────────────────
  const filterByAccess = (info) => {
    // Case A: single result
    if (info.return_id) {
      if (!isAllowed(info.return_id)) {
        throw new Error(
          `You do not have access to return "${info.return_name}". ` +
          `Contact your administrator to request access.`
        )
      }
      return info   // allowed — pass through as-is
    }

    // Case B: candidates list — filter to only allowed returns
    if (info.candidates) {
      const allowed = info.candidates.filter(c => isAllowed(c.return_id))

      if (allowed.length === 0) {
        throw new Error(
          `No accessible returns found matching "${returnName}". ` +
          `Contact your administrator to request access.`
        )
      }

      // If only one candidate left after filter, auto-select it
      if (allowed.length === 1) {
        return { ...info, candidates: allowed, _autoSelect: true }
      }

      return { ...info, candidates: allowed }
    }

    return info
  }

  // ─── API handlers ────────────────────────────────────────────────────────

  const handleFindReturn = async () => {
    const name = returnName.trim()
    if (!name) return

    // Block search until allowed forms are loaded
    if (authLoading) {
      setError('Loading user permissions, please wait...')
      return
    }
    if (authError) {
      setError(authError)
      return
    }

    setLoading(true)
    setError('')
    setCandidates(null)

    try {
      const raw = await findReturnTables(name, loginId)
      const info = filterByAccess(raw)         // ← filter here

      if (info.candidates) {
        // If only 1 allowed candidate, auto-select instead of showing pick-list
        if (info._autoSelect) {
          const only = info.candidates[0]
          const full = await findReturnTables(only.return_name, loginId)
          setReturnInfo(full)
          setTableName(full.tables?.[0]?.table_name ?? '')
          setStep(VARIANCE_STEPS.TABLE)
        } else {
          setCandidates(info.candidates)
          setStep(VARIANCE_STEPS.DISAMBIGUATE)
        }
      } else {
        setReturnInfo(info)
        setTableName(info.tables?.[0]?.table_name ?? '')
        setStep(VARIANCE_STEPS.TABLE)
      }
    } catch (err) {
      setError(err.message || 'Failed to find return.')
    } finally {
      setLoading(false)
    }
  }

  const handleSelectCandidate = async (candidate) => {
    setLoading(true)
    setError('')
    setCandidates(null)
    try {
      const info = await findReturnTables(candidate.return_name, loginId)
      if (info.candidates) {
        const best = info.candidates.find(c => c.has_mapping && isAllowed(c.return_id))
        if (!best) throw new Error('No accessible table mapping available for this return.')
        const retry = await findReturnTables(best.return_name, loginId)
        setReturnInfo(retry)
        setTableName(retry.tables?.[0]?.table_name ?? '')
      } else {
        setReturnInfo(info)
        setTableName(info.tables?.[0]?.table_name ?? '')
      }
      setStep(VARIANCE_STEPS.TABLE)
    } catch (err) {
      setError(err.message || 'Failed to load return.')
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
        return_id: returnInfo.return_id,
        table_mapping_path: returnInfo.table_mapping_path,
        table_name: tableName,
        reporting_date: dateStr.trim(),
        reporting_period: periods,
        comparison_mode: comparisonMode,
      }, loginId)
      setResult(res)
      setStep(VARIANCE_STEPS.RESULT)
      setTableState('normal')
      setVizState('normal')
      setVizOpen(true)
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
    setComparisonMode(COMPARISON_MODES.VS_CURRENT)
    setResult(null)
    setError('')
    setCandidates(null)
    setVizOpen(false)
    setTableState('normal')
    setVizState('normal')
  }

  // ─── NLP handlers ────────────────────────────────────────────────────────
  const handleNlpSearch = (query) => {
    const trimmed = query.trim()
    if (trimmed) setReturnName(trimmed)
  }

  const handleVoiceInput = () => { }

  // ─── Viz toggle ──────────────────────────────────────────────────────────
  const handleToggleViz = () => {
    if (vizOpen) {
      vizPanelRef.current?.collapse()
      setVizOpen(false)
      setVizState('normal')
    } else {
      vizPanelRef.current?.expand()
      setVizOpen(true)
      setVizState('normal')
    }
  }

  // ─── Panel resize handlers ────────────────────────────────────────────────
  const getTablePct = () => tablePanelRef.current?.getSize()?.asPercentage ?? 70

  const handleTableExpand = () => {
    if (tableState === 'expanded') {
      tablePanelRef.current?.resize(savedTablePct)
      setTableState('normal')
    } else {
      setSavedTablePct(getTablePct())
      tablePanelRef.current?.resize(92)
      setTableState('expanded')
      if (vizOpen) setVizState('minimized')
    }
  }

  const handleTableMinimize = () => {
    if (tableState === 'minimized') {
      tablePanelRef.current?.resize(savedTablePct)
      setTableState('normal')
    } else {
      setSavedTablePct(getTablePct())
      tablePanelRef.current?.collapse()
      setTableState('minimized')
    }
  }

  const handleVizExpand = () => {
    if (vizState === 'expanded') {
      tablePanelRef.current?.resize(savedTablePct)
      setVizState('normal')
      setTableState('normal')
    } else {
      setSavedTablePct(getTablePct())
      tablePanelRef.current?.resize(8)
      setVizState('expanded')
      setTableState('minimized')
    }
  }

  const handleVizMinimize = () => {
    if (vizState === 'minimized') {
      vizPanelRef.current?.expand()
      setVizState('normal')
    } else {
      vizPanelRef.current?.collapse()
      setVizState('minimized')
      setVizOpen(false)
    }
  }

  // In LayoutContainer.jsx — replace the useEffect (around line 60)
  useEffect(() => {
    setAuthLoading(true)
    getMyReturns()           // ← no args — reads loginId from sessionStorage via getAuthParams()
      .then((data) => {
        if (!data.login_id) {
          setAuthError('No loginId found. Contact your administrator.')
          setAuthLoading(false)
          return
        }
        setAllowedFormIds(new Set(data.allowed_forms || []))
        setAuthLoading(false)
      })
      .catch((err) => {
        setAuthError(err.message || 'Failed to load user permissions.')
        setAuthLoading(false)
      })
  }, [])   // ← no dependency on loginId prop

  // ─── Render ──────────────────────────────────────────────────────────────

  // While loading permissions show a spinner
  if (authLoading) {
    return (
      <div className="lc-idle">
        <span className="spinner lc-idle-spinner" />
        <div className="lc-idle-title">Loading permissions…</div>
        <div className="lc-idle-sub">Checking your access rights</div>
      </div>
    )
  }

  // If auth completely failed (no loginId, user not found)
  if (authError && !allowedFormIds) {
    return (
      <div className="lc-idle">
        <div className="lc-idle-icon">🔒</div>
        <div className="lc-idle-title">Access Error</div>
        <div className="lc-idle-sub">{authError}</div>
      </div>
    )
  }

  return (
    <div className="lc-root">

      {/* ── Compact control toolbar ─────────────────────────────────── */}
      <ControlBar
        step={step}
        returnName={returnName} setReturnName={setReturnName}
        returnInfo={returnInfo} tables={tables}
        tableName={tableName} setTableName={setTableName}
        dateStr={dateStr} setDateStr={setDateStr}
        dateHint={dateHint}
        periods={periods} setPeriods={setPeriods}
        comparisonMode={comparisonMode} setComparisonMode={setComparisonMode}
        loading={loading} error={error}
        candidates={candidates}
        handleFindReturn={handleFindReturn}
        handleCompute={handleCompute}
        handleReset={handleReset}
        handleSelectCandidate={handleSelectCandidate}
        onVisualize={handleToggleViz}
        vizOpen={vizOpen}
        nlpQuery={nlpQuery}
        setNlpQuery={setNlpQuery}
        handleNlpSearch={handleNlpSearch}
        handleVoiceInput={handleVoiceInput}
      />

      {/* ── Analysis area ────────────────────────────────────────────── */}
      {result && (
        <div className="lc-bottom lc-bottom-enter">
          <Group
            orientation="horizontal"
            style={{ height: '100%' }}
            defaultLayout={[55, 45]}
          >
            <Panel
              panelRef={tablePanelRef}
              defaultSize={55}
              minSize={20}
              collapsible
              collapsedSize={4}
              onResize={(size) => {
                if (size?.asPercentage <= 4 && tableState !== 'minimized') setTableState('minimized')
                else if (size?.asPercentage > 4 && tableState === 'minimized') setTableState('normal')
              }}
              style={{ overflow: 'hidden' }}
            >
              <TablePanel
                result={result}
                tableState={tableState}
                onExpand={handleTableExpand}
                onMinimize={handleTableMinimize}
                onReset={handleReset}
                loading={loading}
              />
            </Panel>

            <Separator className="lc-vresize-handle">
              <div className="lc-vresize-bar" />
            </Separator>

            <Panel
              panelRef={vizPanelRef}
              defaultSize={45}
              minSize={20}
              collapsible
              collapsedSize={4}
              onResize={(size) => {
                if (size?.asPercentage <= 4) {
                  if (vizOpen) setVizOpen(false)
                  if (vizState !== 'minimized') setVizState('minimized')
                } else {
                  if (!vizOpen) setVizOpen(true)
                  if (vizState === 'minimized') setVizState('normal')
                }
              }}
              style={{ overflow: 'hidden' }}
            >
              <VisualizationPanel
                result={result}
                vizState={vizState}
                vizOpen={vizOpen}
                onExpand={handleVizExpand}
                onMinimize={handleVizMinimize}
              />
            </Panel>
          </Group>
        </div>
      )}

      {/* ── Pre-compute placeholder ──────────────────────────────────── */}
      {!result && !loading && (
        <div className="lc-idle">
          <div className="lc-idle-icon">📊</div>
          <div className="lc-idle-title">No analysis yet</div>
          <div className="lc-idle-sub">
            {returnInfo
              ? 'Select a table, enter a reporting date, then click Compute Variance'
              : 'Search for a return above to get started'}
          </div>
        </div>
      )}

      {!result && loading && (
        <div className="lc-idle">
          <span className="spinner lc-idle-spinner" />
          <div className="lc-idle-title">Computing variance…</div>
          <div className="lc-idle-sub">Querying Oracle and building the comparison table</div>
        </div>
      )}

    </div>
  )
}