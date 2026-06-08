/**
 * LayoutContainer — main layout orchestrator.
 *
 * Layout:
 *   TOP    : ControlBar — compact two-row toolbar, always visible (~10-12% height)
 *   BOTTOM : Analysis area — hidden until first compute succeeds.
 *            Vertical Group: TablePanel (top) | Separator | VisualizationPanel (bottom).
 *            Viz panel starts collapsed; expands when user clicks "Visualize Data".
 */
import { useState, useRef } from 'react'
import { Panel, Group, Separator } from 'react-resizable-panels'

import { findReturnTables, computeVariance } from '../api.js'
import { VARIANCE_STEPS, dateHintForFreq } from '../types.js'

import ControlBar         from './ControlBar.jsx'
import TablePanel         from './TablePanel.jsx'
import VisualizationPanel from './VisualizationPanel.jsx'

export default function LayoutContainer() {
  // ─── NLP bar state ───────────────────────────────────────────────────────
  const [nlpQuery, setNlpQuery] = useState('')

  // ─── Wizard state ────────────────────────────────────────────────────────
  const [step,       setStep]       = useState(VARIANCE_STEPS.RETURN_NAME)
  const [returnName, setReturnName] = useState('')
  const [returnInfo, setReturnInfo] = useState(null)
  const [candidates, setCandidates] = useState(null)   // multi-match pick-list
  const [tableName,  setTableName]  = useState('')
  const [dateStr,    setDateStr]    = useState('')
  const [periods,    setPeriods]    = useState(1)
  const [result,     setResult]     = useState(null)
  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState('')

  // ─── Panel state ─────────────────────────────────────────────────────────
  const tablePanelRef = useRef(null)
  const vizPanelRef   = useRef(null)
  const [savedTablePct, setSavedTablePct] = useState(70)
  const [tableState,    setTableState]    = useState('normal') // normal | expanded | minimized
  const [vizState,      setVizState]      = useState('normal') // normal | expanded | minimized
  const [vizOpen,       setVizOpen]       = useState(false)   // viz panel visible?

  // ─── Derived ─────────────────────────────────────────────────────────────
  const tables = (returnInfo?.tables || []).filter(
    (t, i, arr) => t.table_name && arr.findIndex((x) => x.table_name === t.table_name) === i
  )
  const dateHint = returnInfo ? dateHintForFreq(returnInfo.report_freq) : { example: '', hint: '' }

  // ─── API handlers ────────────────────────────────────────────────────────
  const handleFindReturn = async () => {
    const name = returnName.trim()
    if (!name) return
    setLoading(true)
    setError('')
    setCandidates(null)
    try {
      const info = await findReturnTables(name)
      if (info.candidates) {
        // Multiple plausible matches — show pick-list
        setCandidates(info.candidates)
        setStep(VARIANCE_STEPS.DISAMBIGUATE)
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

  // Called when user picks one item from the disambiguation list
  const handleSelectCandidate = async (candidate) => {
    setLoading(true)
    setError('')
    setCandidates(null)
    try {
      // Re-query by exact return_id to get full info + tables
      const info = await findReturnTables(candidate.return_name)
      if (info.candidates) {
        // Still ambiguous (shouldn’t happen with exact name) — just pick first usable
        const best = info.candidates.find(c => c.has_mapping)
        if (!best) throw new Error('No table mapping available for this return.')
        const retry = await findReturnTables(best.return_name)
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
        return_id:          returnInfo.return_id,
        table_mapping_path: returnInfo.table_mapping_path,
        table_name:         tableName,
        reporting_date:     dateStr.trim(),
        reporting_period:   periods,
      })
      setResult(res)
      setStep(VARIANCE_STEPS.RESULT)
      // Reset panel states; auto-open viz panel alongside table
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
    setResult(null)
    setError('')
    setCandidates(null)
    setVizOpen(false)
    setTableState('normal')
    setVizState('normal')
  }

  // ─── NLP handlers ────────────────────────────────────────────────────────
  const handleNlpSearch = (query) => {
    // Placeholder: pre-fill the return name field from NLP query text
    const trimmed = query.trim()
    if (trimmed) {
      setReturnName(trimmed)
    }
  }

  const handleVoiceInput = () => {
    // Placeholder for voice input integration
  }

  // ─── Viz toggle (from "Visualize Data" / "Hide Viz" button) ─────────────
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

  // ─── Table panel expand / minimize ──────────────────────────────────────
  const getTablePct = () => tablePanelRef.current?.getSize()?.asPercentage ?? 70

  const handleTableExpand = () => {
    if (tableState === 'expanded') {
      tablePanelRef.current?.resize(savedTablePct)
      setTableState('normal')
      setVizState(vizOpen ? 'normal' : 'normal')
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

  // ─── Viz panel expand / minimize ────────────────────────────────────────
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

  // ─── Render ──────────────────────────────────────────────────────────────
  return (
    <div className="lc-root">

      {/* ── Compact control toolbar ─────────────────────────────────── */}
      <ControlBar
        step={step}
        returnName={returnName}   setReturnName={setReturnName}
        returnInfo={returnInfo}   tables={tables}
        tableName={tableName}     setTableName={setTableName}
        dateStr={dateStr}         setDateStr={setDateStr}
        dateHint={dateHint}
        periods={periods}         setPeriods={setPeriods}
        loading={loading}         error={error}
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

      {/* ── Analysis area — appears after first successful compute ───── */}
      {result && (
        <div className="lc-bottom lc-bottom-enter">
          <Group
            orientation="horizontal"
            style={{ height: '100%' }}
            defaultLayout={[55, 45]}
          >
            {/* Table panel — left */}
            <Panel
              panelRef={tablePanelRef}
              defaultSize={55}
              minSize={20}
              collapsible
              collapsedSize={4}
              onResize={(size) => {
                if (size?.asPercentage <= 4 && tableState !== 'minimized') {
                  setTableState('minimized')
                } else if (size?.asPercentage > 4 && tableState === 'minimized') {
                  setTableState('normal')
                }
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

            {/* Vertical resize handle */}
            <Separator className="lc-vresize-handle">
              <div className="lc-vresize-bar" />
            </Separator>

            {/* Viz panel — right, starts collapsed */}
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

