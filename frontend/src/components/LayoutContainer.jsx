/**
 * LayoutContainer — main layout orchestrator.
 *
 * Authorization flow:
 *   1. On mount → calls GET /auth/my-returns (auth params from src/auth/)
 *      → stores allowedFormIds as a Set (e.g. Set{"2001","2007","4016",...})
 *
 *   2. On search → calls GET /variance/find?return_name=...
 *      → filters the results against allowedFormIds BEFORE showing to user:
 *          • Single result  → shown only if return_id is in allowedFormIds
 *          • Candidates list → filtered to only allowed return_ids
 *          • If nothing passes filter → shows "no access" error
 *
 * Layout:
 *   TOP    : ControlBar — compact two-row toolbar, always visible
 *   BOTTOM : Analysis area — hidden until first compute succeeds.
 */
import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { Panel, Group, Separator } from 'react-resizable-panels'

import { findReturnTables, computeVariance, getMyReturns, resolveNlQuery, getAvailableDates, SKIP_ANSWER } from '../api.js'
import { VARIANCE_STEPS, COMPARISON_MODES } from '../types.js'

import ControlBar         from './ControlBar.jsx'
import TablePanel         from './TablePanel.jsx'
import VisualizationPanel from './VisualizationPanel.jsx'
import NoticeToast        from './NoticeToast.jsx'

// Stable identity, so the "no hidden columns" case hands the same array down
// on every render instead of a fresh literal.
const EMPTY_COLS = []

// ─── Hidden-column persistence ──────────────────────────────────────────────
// Keyed on table name so a hidden set survives switching tables and reloading,
// versioned so the stored shape can change later. Every access is wrapped: a
// private window, disabled site data, or a hand-corrupted value must degrade to
// "nothing hidden", never blank the results panel.
const COL_VIS_KEY = (tableName) => `dvm.hiddenCols.v1:${tableName}`

function loadHiddenCols(tableName, availableCols) {
  if (!tableName) return EMPTY_COLS
  try {
    const raw = localStorage.getItem(COL_VIS_KEY(tableName))
    if (!raw) return EMPTY_COLS
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return EMPTY_COLS
    // Intersected with the columns this result actually has: a stored name from
    // an older schema would otherwise sit in the hidden set forever, invisibly
    // inflating the "n hidden" count against a column that no longer exists.
    const available = new Set(availableCols.map((c) => c.toUpperCase()))
    const kept = parsed.filter((c) => typeof c === 'string' && available.has(c.toUpperCase()))
    return kept.length ? kept : EMPTY_COLS
  } catch {
    return EMPTY_COLS
  }
}

function saveHiddenCols(tableName, hidden) {
  if (!tableName) return
  try {
    if (hidden.length) {
      localStorage.setItem(COL_VIS_KEY(tableName), JSON.stringify(hidden))
    } else {
      // Removed rather than stored as [] so keys don't accumulate for every
      // table the user has merely looked at.
      localStorage.removeItem(COL_VIS_KEY(tableName))
    }
  } catch {
    /* storage unavailable — visibility still works for this session */
  }
}

// loginId/tenantId are the RESOLVED identity from src/auth/, passed in for
// display and access gating only — api.js reads them from the auth module
// itself, so no call site needs to forward them.
export default function LayoutContainer({ loginId = '', tenantId = '', uid = '' }) {

  // ─── Allowed form IDs for this user ──────────────────────────────────────
  // Populated once on mount from GET /auth/my-returns
  // e.g. Set { "2001", "2007", "4016", "6001", ... }
  const [allowedFormIds,    setAllowedFormIds]    = useState(null)   // null = not loaded yet
  const [allowedFormNames,  setAllowedFormNames]  = useState(null)   // null = not loaded yet
  const [authLoading,       setAuthLoading]       = useState(true)
  const [authError,         setAuthError]         = useState('')

  // ─── NLP bar state ───────────────────────────────────────────────────────
  const [nlpQuery, setNlpQuery] = useState('')
  // Columns resolved by the NL layer, valid only while tableName still matches
  // the table they were resolved for (cleared on any manual re-selection).
  const [nlColumns, setNlColumns] = useState(null)
  // Set when the backend can't confidently resolve the query to one table —
  // { question, options, resolvedContext }. Rendered below the NLP input;
  // independent of the manual wizard's step/candidates (same reasoning as
  // nlpQuery/nlColumns above — the NLP flow must stay independent).
  const [nlpClarification, setNlpClarification] = useState(null)
  // What the backend understood the query to mean (return/metric/date/scope) —
  // shown back to the user under the NLP bar (ControlBar's NlpInterpretation)
  // so a wrong resolution is visible rather than only inferable from whether
  // the resulting numbers look right. Present on BOTH clarification and result
  // responses, so it stays on screen across a clarification round-trip.
  const [nlpInterpretation, setNlpInterpretation] = useState(null)

  // ─── Wizard state ────────────────────────────────────────────────────────
  const [step,       setStep]       = useState(VARIANCE_STEPS.RETURN_NAME)
  const [returnName, setReturnName] = useState('')
  const [returnInfo, setReturnInfo] = useState(null)
  const [candidates, setCandidates] = useState(null)
  const [tableName,  setTableName]  = useState('')
  // Reporting dates ticked in the manual Date checkbox list (ControlBar's
  // DateField), newest first, at most 3. The newest is the current period and
  // the rest are compared against it — the backend applies the same rule (see
  // calculate_variance's comparison_dates). Replaces the old single dateStr:
  // one date is just a selection of length 1, so there is no second source of
  // truth to keep in sync.
  const [selectedDates, setSelectedDates] = useState([])
  const [availableDates, setAvailableDates] = useState([])
  const [datesLoading,   setDatesLoading]   = useState(false)
  const [periods,    setPeriods]    = useState(1)
  const [comparisonMode, setComparisonMode] = useState(COMPARISON_MODES.VS_CURRENT)
  const [result,     setResult]     = useState(null)
  // ─── Column visibility ───────────────────────────────────────────────────
  // Signature and hidden list are stored TOGETHER on purpose. `result` is
  // replaced wholesale in three places (handleCompute, handleReset, the NLP
  // handler), and resetting the hidden set from a useEffect would render one
  // frame with the previous table's hidden columns applied to the new table's
  // schema. Comparing the stored signature during render instead makes a stale
  // set unreachable, with no reset code at any setResult call site to forget.
  const [colVis, setColVis] = useState({ sig: null, hidden: EMPTY_COLS })
  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState('')
  const [notice,     setNotice]     = useState('')

  // ─── Panel state ─────────────────────────────────────────────────────────
  const tablePanelRef = useRef(null)
  const vizPanelRef   = useRef(null)
  const [tableState,    setTableState]    = useState('normal')
  const [vizState,      setVizState]      = useState('normal')
  const [vizOpen,       setVizOpen]       = useState(false)

  // ─── Derived ─────────────────────────────────────────────────────────────
  const tables = (returnInfo?.tables || []).filter(
    (t, i, arr) => t.table_name && arr.findIndex((x) => x.table_name === t.table_name) === i
  )

  // ─── Column visibility, derived ──────────────────────────────────────────
  // The signature includes the column list, not just the table name, so any
  // change to the column universe (a vs_current/sequential switch, the NLP
  // path's own column trim, a schema change) self-heals. A recompute of the
  // same table with the same columns keeps the signature — and therefore keeps
  // the user's hidden set — which is what makes changing dates non-destructive.
  const resultDisplayCols = result?.display_columns ?? result?.columns ?? EMPTY_COLS
  const colSig = result && !result.error
    ? `${result.table_name}|${result.comparison_mode ?? 'vs_current'}|${resultDisplayCols.join(',')}`
    : null
  // Reading straight from localStorage when the signature doesn't match is
  // what makes a NEW result arrive with its own stored preferences already
  // applied, on the very first render, with no flash of the wrong set.
  //
  // Memoized because this branch stays live until the user's FIRST toggle on a
  // result: without it, every unrelated re-render would re-read storage,
  // re-parse the JSON and hand children a brand-new array identity.
  const storedHidden = useMemo(
    () => loadHiddenCols(result?.table_name, resultDisplayCols),
    // colSig covers both inputs — it is built from the table name and the
    // column list — so it is the whole dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [colSig]
  )
  const hiddenCols = colSig && colVis.sig === colSig ? colVis.hidden : storedHidden

  // Every handler stamps the CURRENT signature, so a write can never be
  // attributed to a result it wasn't made against.
  const commitHidden = useCallback(
    (next) => {
      if (!colSig) return
      saveHiddenCols(result?.table_name, next)
      setColVis({ sig: colSig, hidden: next.length ? next : EMPTY_COLS })
    },
    [colSig, result?.table_name]
  )

  const handleToggleCol = useCallback(
    (col) => {
      const upper = col.toUpperCase()
      const isHidden = hiddenCols.some((c) => c.toUpperCase() === upper)
      commitHidden(
        isHidden
          ? hiddenCols.filter((c) => c.toUpperCase() !== upper)
          // Kept in the result's own column order rather than click order, so
          // the stored list stays readable and stable.
          : resultDisplayCols.filter(
              (c) => c.toUpperCase() === upper || hiddenCols.some((h) => h.toUpperCase() === c.toUpperCase())
            )
      )
    },
    [commitHidden, hiddenCols, resultDisplayCols]
  )

  // Idempotent — the header × can be clicked on an already-hidden column only
  // via a race, but this keeps the set from growing duplicates either way.
  const handleHideCol = useCallback(
    (col) => {
      const upper = col.toUpperCase()
      if (hiddenCols.some((c) => c.toUpperCase() === upper)) return
      handleToggleCol(col)
    },
    [handleToggleCol, hiddenCols]
  )

  const handleShowAllCols = useCallback(() => commitHidden(EMPTY_COLS), [commitHidden])
  const handleHideAllCols = useCallback(
    () => commitHidden([...resultDisplayCols]),
    [commitHidden, resultDisplayCols]
  )
  // ─── Fetch available dates whenever the selected table changes ──────────
  // Feeds the manual Date dropdown (ControlBar's DateField) with the real
  // submission dates on file, instead of a free calendar where most picks
  // return zero rows.
  useEffect(() => {
    if (!returnInfo || !tableName) {
      setAvailableDates([])
      return
    }

    let cancelled = false
    setDatesLoading(true)
    setSelectedDates([]) // stale dates from a previous table must not linger

    getAvailableDates(returnInfo.return_id, returnInfo.table_mapping_path, tableName)
      .then((data) => {
        if (cancelled) return
        setAvailableDates(data.dates || [])
      })
      .catch((err) => {
        if (cancelled) return
        setAvailableDates([])
        console.warn('Failed to load available dates:', err.message)
      })
      .finally(() => {
        if (!cancelled) setDatesLoading(false)
      })

    return () => { cancelled = true }
  }, [returnInfo, tableName, loginId])

  // ─── Step 1: Fetch allowed form IDs on mount ─────────────────────────────
  useEffect(() => {
    if (!loginId) {
      setAllowedFormIds(new Set())
      setAuthLoading(false)
      setAuthError('')
      return
    }

    setAuthLoading(true)
    getMyReturns()
      .then((data) => {
        // data.allowed_forms = ["2001", "2007", "4016", ...]
        setAllowedFormIds(new Set(data.allowed_forms || []))
        setAuthLoading(false)
        setAuthError('')
      })
      .catch((err) => {
        // If the backend auth layer is disabled, allow the app to proceed without access filtering.
        setAllowedFormIds(new Set())
        setAuthLoading(false)
        setAuthError('')
        console.warn('Auth permissions unavailable, continuing without access filtering:', err.message)
      })
  }, [loginId])

  // ─── Filter helper — check if a return_id is allowed ─────────────────────
  const isAllowed = (returnId) => {
    if (!allowedFormIds) return true
    return allowedFormIds.size === 0 || allowedFormIds.has(String(returnId))
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

    // Continue even if auth metadata is unavailable; the backend toggle may be off.
    if (authLoading) {
      setError('Loading user permissions, please wait...')
      return
    }

    setLoading(true)
    setError('')
    setCandidates(null)
    setNlColumns(null)

    try {
      const raw  = await findReturnTables(name)
      const info = filterByAccess(raw)         // ← filter here

      if (info.candidates) {
        // If only 1 allowed candidate, auto-select instead of showing pick-list
        if (info._autoSelect) {
          const only = info.candidates[0]
          const full = await findReturnTables(only.return_name)
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
    setNlColumns(null)
    try {
      const info = await findReturnTables(candidate.return_name)
      if (info.candidates) {
        const best = info.candidates.find(c => c.has_mapping && isAllowed(c.return_id))
        if (!best) throw new Error('No accessible table mapping available for this return.')
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

  // calculate_variance.py's comparison periods are computed by pure
  // calendar-quarter arithmetic (e.g. Dec -> Sep) — if a return was actually
  // filed off-cycle (e.g. 30-Nov instead of 30-Sep), that date query returns
  // zero rows and every row's "previous" entry for it silently comes back
  // empty, with nothing telling the user why. missing_periods (added to the
  // compute response) surfaces exactly which requested dates had no
  // submission at all, so a popup can explain it instead of a silent gap.
  const applyMissingPeriodsNotice = (res) => {
    if (res?.missing_periods?.length) {
      const list = res.missing_periods.join(', ')
      const plural = res.missing_periods.length > 1 ? 's' : ''
      setNotice(`No submission on file for comparison period${plural}: ${list}.`)
    }
  }

  const handleCompute = async () => {
    if (!returnInfo || !tableName || selectedDates.length === 0) return
    setLoading(true)
    setError('')
    try {
      // Only honor NL-resolved columns while tableName still matches the
      // table they were resolved for (a manual table switch clears nlColumns).
      const selectedColumns =
        nlColumns && nlColumns.tableName === tableName ? nlColumns.columns : undefined

      const res = await computeVariance({
        return_id:          returnInfo.return_id,
        table_mapping_path: returnInfo.table_mapping_path,
        table_name:         tableName,
        // The newest ticked date. Sent alongside comparison_dates (which the
        // backend treats as authoritative and re-derives this from) so the
        // request is still valid against the unchanged required field.
        reporting_date:     selectedDates[0],
        // With ONE ticked date this is the unchanged derived path: walk back
        // `periods` periods from that date. With several, comparison_dates
        // takes over and the backend ignores reporting_period entirely.
        reporting_period:   periods,
        comparison_dates:   selectedDates.length > 1 ? selectedDates : undefined,
        selected_columns:   selectedColumns,
        comparison_mode:    comparisonMode,
      })
      setResult(res)
      // A manual compute replaces the table on screen, so the "Understood as"
      // chips from an earlier NLP query would now be describing a different
      // result. Clearing them keeps the attribution honest — the chips are the
      // only thing marking a result as inferred rather than chosen.
      setNlpInterpretation(null)
      applyMissingPeriodsNotice(res)
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
    setSelectedDates([])
    setPeriods(1)
    setComparisonMode(COMPARISON_MODES.VS_CURRENT)
    setResult(null)
    setError('')
    setNotice('')
    setCandidates(null)
    setNlColumns(null)
    setNlpClarification(null)
    setNlpInterpretation(null)
    setVizOpen(false)
    setTableState('normal')
    setVizState('normal')
  }

  // ─── NLP handlers ────────────────────────────────────────────────────────
  // One-shot: resolveNlQuery does resolution AND computation server-side
  // (embedding+LLM column/table pick -> date/period intent resolved against
  // real data -> compute_variance) and returns a result shaped exactly like
  // /variance/compute's response. This is a display-only path: it feeds the
  // result straight into the table/visualization panels and deliberately
  // does NOT touch returnName/returnInfo/tableName/selectedDates/periods — those
  // belong to the manual return/table/date wizard and must stay independent
  // of whatever the NLP bar resolved, so neither flow overrides the other.
  // `clarificationOverride` lets a caller force which (if any) clarification
  // this request continues — omit it to use whatever's currently pending
  // (the normal select/skip case), or pass `null` explicitly to start a
  // brand-new top-level query instead of answering the pending one (see
  // handleClarificationOthers below: the user's extra info should be
  // re-resolved from scratch, not treated as an answer to the old prompt).
  const handleNlpSearch = async (query, selectedOption, clarificationOverride) => {
    const trimmed = query.trim()
    if (!trimmed) return

    if (authLoading) {
      setError('Loading user permissions, please wait...')
      return
    }

    const activeClarification =
      clarificationOverride !== undefined ? clarificationOverride : nlpClarification

    // A brand-new question clears the page before it runs.
    //
    // Without this, the previous answer's table and chart stay on screen for
    // the whole round-trip and — if the new query ends in a clarification, a
    // refusal, or an error — they stay there indefinitely, silently
    // mislabelled as the response to the question just typed. Nothing on the
    // table itself says which query produced it, so a stale result is
    // indistinguishable from a fresh one.
    //
    // Answering a pending clarification is NOT a new question: it is the
    // second half of the one already in flight, so its state is left alone.
    if (!activeClarification) {
      setResult(null)
      setNotice('')
      setNlpClarification(null)
      setNlpInterpretation(null)
      setVizOpen(false)
      setTableState('normal')
      setVizState('normal')
    }

    setLoading(true)
    setError('')

    try {
      const res = await resolveNlQuery(trimmed, {
        dimension:            activeClarification?.dimension,
        clarificationAnswer:  selectedOption?.id,
        resolvedContext:      activeClarification?.resolvedContext,
      })

      // Set before the clarification early-return so the chips render on a
      // clarification round too — that's exactly when knowing what was
      // understood so far is most useful to the user answering the question.
      setNlpInterpretation(res.interpretation ?? null)

      if (res.needs_clarification) {
        setNlpClarification({
          dimension:       res.dimension,
          question:        res.question,
          options:         res.options,
          skippable:       res.skippable,
          allowOther:      res.allow_other,
          resolvedContext: res.resolved_context,
        })
        return
      }

      setNlpClarification(null)
      setResult(res)
      applyMissingPeriodsNotice(res)
      setTableState('normal')
      setVizState('normal')
      setVizOpen(true)
    } catch (err) {
      setError(err.message || 'Could not resolve this query.')
    } finally {
      setLoading(false)
    }
  }

  const handleClarificationSelect = (option) => {
    handleNlpSearch(nlpQuery, option)
  }

  const handleClarificationSkip = () => {
    handleNlpSearch(nlpQuery, { id: SKIP_ANSWER })
  }

  const handleClarificationCancel = () => setNlpClarification(null)

  // User typed extra detail into the "Others" box instead of picking a
  // listed return — fold it into the original query and re-resolve as a
  // brand-new query (clarificationOverride=null) rather than answering the
  // pending clarification, since free text isn't a valid return_id.
  const handleClarificationOthers = (extraInfo) => {
    const extra = extraInfo.trim()
    if (!extra) return
    const combined = `${nlpQuery} ${extra}`.trim()
    setNlpQuery(combined)
    setNlpClarification(null)
    handleNlpSearch(combined, undefined, null)
  }

  const handleVoiceInput = () => {}

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
  // "Expand panel X" means "make X take (nearly) the whole row" — the ONLY
  // way to do that in a 2-panel Group is to shrink the SIBLING, not to
  // resize() this panel to a hardcoded 92%. resize() ignored that: both
  // panels declare minSize={20}, so pushing table to 92 (leaving viz at ~8,
  // below viz's own minSize) or viz to 92 (leaving table at ~8) fought the
  // library's own minSize/collapsible enforcement — confirmed empirically to
  // invert the result (clicking table's expand button actually shrank the
  // table to ~7% and blew viz up to ~93%, the opposite of the intent).
  // collapse()/expand() are the library's dedicated bypass for exactly this
  // ("collapse the sibling to its collapsedSize, independent of minSize"),
  // matching the pattern handleToggleViz below already used successfully —
  // so no more manual resize-to-magic-number and no savedTablePct bookkeeping
  // needed: expand() natively restores a collapsed panel to its pre-collapse
  // size, and the sibling's size falls out of that for free.
  const handleTableExpand = () => {
    if (tableState === 'expanded') {
      vizPanelRef.current?.expand()
      setTableState('normal')
      if (vizState === 'minimized') setVizState('normal')
    } else {
      vizPanelRef.current?.collapse()
      setTableState('expanded')
      if (vizOpen) setVizState('minimized')
    }
  }

  const handleTableMinimize = () => {
    if (tableState === 'minimized') {
      tablePanelRef.current?.expand()
      setTableState('normal')
    } else {
      tablePanelRef.current?.collapse()
      setTableState('minimized')
    }
  }

  const handleVizExpand = () => {
    if (vizState === 'expanded') {
      tablePanelRef.current?.expand()
      setVizState('normal')
      setTableState('normal')
    } else {
      tablePanelRef.current?.collapse()
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
        returnName={returnName}   setReturnName={setReturnName}
        returnInfo={returnInfo}   tables={tables}
        tableName={tableName}     setTableName={setTableName}
        selectedDates={selectedDates} setSelectedDates={setSelectedDates}
        availableDates={availableDates} datesLoading={datesLoading}
        periods={periods}         setPeriods={setPeriods}
        comparisonMode={comparisonMode} setComparisonMode={setComparisonMode}
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
        nlpClarification={nlpClarification}
        nlpInterpretation={nlpInterpretation}
        onClarificationSelect={handleClarificationSelect}
        onClarificationSkip={handleClarificationSkip}
        onClarificationCancel={handleClarificationCancel}
        onClarificationOthers={handleClarificationOthers}
      />

      {/* ── Analysis area ────────────────────────────────────────────── */}
      {/* react-resizable-panels treats a bare number on minSize/maxSize/
          collapsedSize as PIXELS, not percent (only defaultSize/defaultLayout
          use percentages for a bare number) — collapsedSize={4} was 4 PIXELS,
          not 4%, so "minimized" panels shrank to an unusably thin sliver
          whose header/restore button got visually overlapped by the sibling
          panel's content (confirmed: an unclickable "Restore panel" button).
          minSize={20} had the same issue (20px is effectively no floor at
          all). Always use string percentages ("20%"/"4%") for these props. */}
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
              minSize="20%"
              collapsible
              collapsedSize="4%"
              onResize={() => {
                // isCollapsed() is the library's own authoritative flag —
                // comparing size.asPercentage against the same 4 used for
                // collapsedSize put the "is it collapsed?" check exactly on
                // the boundary the collapse settles at, so float rounding on
                // the resize event could land a hair above 4 and immediately
                // flip tableState back to 'normal' right after minimizing
                // (confirmed: two renders back-to-back, minimized -> normal).
                const collapsed = tablePanelRef.current?.isCollapsed() ?? false
                setTableState((prev) => {
                  if (collapsed) return prev === 'minimized' ? prev : 'minimized'
                  return prev === 'minimized' ? 'normal' : prev
                })
              }}
              style={{ overflow: 'hidden' }}
            >
              <TablePanel
                result={result}
                hiddenCols={hiddenCols}
                onToggleCol={handleToggleCol}
                onHideCol={handleHideCol}
                onShowAllCols={handleShowAllCols}
                onHideAllCols={handleHideAllCols}
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
              minSize="20%"
              collapsible
              collapsedSize="4%"
              onResize={() => {
                // See the table Panel's onResize comment — isCollapsed() is
                // the library's own flag, avoiding the same boundary race a
                // size.asPercentage<=4 comparison had against collapsedSize="4%".
                const collapsed = vizPanelRef.current?.isCollapsed() ?? false
                if (collapsed) {
                  if (vizOpen) setVizOpen(false)
                  setVizState((prev) => (prev === 'minimized' ? prev : 'minimized'))
                } else {
                  if (!vizOpen) setVizOpen(true)
                  setVizState((prev) => (prev === 'minimized' ? 'normal' : prev))
                }
              }}
              style={{ overflow: 'hidden' }}
            >
              <VisualizationPanel
                result={result}
                hiddenCols={hiddenCols}
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

      <NoticeToast message={notice} onDismiss={() => setNotice('')} />

    </div>
  )
}