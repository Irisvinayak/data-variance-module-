// import { useEffect, useRef } from 'react'
// import flatpickr from 'flatpickr'
// import 'flatpickr/dist/flatpickr.min.css'
// import { VARIANCE_STEPS, freqLabel } from '../types.js'

// export default function InputPanel({
//   step,
//   setStep,
//   returnInfo,
//   tables,
//   tableName,
//   setTableName,
//   dateStr,
//   setDateStr,
//   dateHint,
//   periods,
//   setPeriods,
//   loading,
//   error,
//   handleCompute,
//   handleReset,
// }) {
//   console.log('[flatpickr-debug] InputPanel RENDER', { returnInfo: !!returnInfo, step })

//   const flatpickrWrapRef = useRef(null)
//   const fpInstanceRef = useRef(null)

//   // --- Flatpickr init/teardown, now re-runs whenever the wrapper div
//   // actually mounts/unmounts (i.e. when returnInfo/step change), with
//   // console logs so you can see exactly what's happening in devtools.
//   useEffect(() => {
//     console.log('[flatpickr-debug] effect fired', {
//       returnInfo: !!returnInfo,
//       step,
//       hasWindowFlatpickr: !!window.flatpickr,
//       wrapRefPresent: !!flatpickrWrapRef.current,
//       alreadyInitialized: !!fpInstanceRef.current,
//     })

//     if (!window.flatpickr) {
//       console.warn(
//         '[flatpickr-debug] window.flatpickr is undefined — the global script tag did not load, or you are relying on the global instead of the imported module. This is the #1 cause of "calendar does nothing".'
//       )
//     }

//     if (!flatpickrWrapRef.current) {
//       console.warn(
//         '[flatpickr-debug] flatpickrWrapRef.current is null — the wrapper div is not in the DOM yet (likely because returnInfo is falsy or step === RESULT, so this whole block is unmounted).'
//       )
//     }

//     if (fpInstanceRef.current) {
//       console.log(
//         '[flatpickr-debug] instance already exists, skipping re-init.'
//       )
//     }

//     if (
//       window.flatpickr &&
//       flatpickrWrapRef.current &&
//       !fpInstanceRef.current
//     ) {
//       console.log('[flatpickr-debug] initializing flatpickr instance now')

//       fpInstanceRef.current = window.flatpickr(flatpickrWrapRef.current, {
//         wrap: true,
//         dateFormat: 'd-M-Y',
//         defaultDate: dateStr || null,
//         onChange: (_selectedDates, selectedDate) => {
//           console.log('[flatpickr-debug] onChange fired ->', selectedDate)
//           setDateStr(selectedDate)
//         },
//         onOpen: () => {
//           console.log('[flatpickr-debug] calendar opened')
//         },
//         onClose: () => {
//           console.log('[flatpickr-debug] calendar closed')
//         },
//       })

//       console.log(
//         '[flatpickr-debug] init result:',
//         fpInstanceRef.current
//       )
//     }

//     return () => {
//       if (fpInstanceRef.current) {
//         console.log(
//           '[flatpickr-debug] tearing down flatpickr instance (wrapper unmounting or effect re-running)'
//         )
//         fpInstanceRef.current.destroy()
//         fpInstanceRef.current = null
//       }
//     }
//     // Re-run when the wrapper div's presence in the DOM can change.
//     // Previously this was [] which only ran once on InputPanel mount —
//     // if returnInfo was falsy at that point, the ref was null forever
//     // and flatpickr never got attached even after returnInfo arrived.
//   }, [returnInfo, step])

//   // Keep the displayed date in sync if dateStr changes externally
//   // (e.g. via handleReset), since flatpickr in wrap mode doesn't read
//   // from a controlled React value automatically.
//   useEffect(() => {
//     if (fpInstanceRef.current) {
//       console.log('[flatpickr-debug] syncing external dateStr ->', dateStr)
//       fpInstanceRef.current.setDate(dateStr || null, false)
//     }
//   }, [dateStr])

//   useEffect(() => {
//     if (window.lucide) {
//       window.lucide.createIcons()
//     }
//   })

//   const noTables =
//     returnInfo && Array.isArray(tables) && tables.length === 0

//   const canCompute = !!(
//     returnInfo &&
//     tableName &&
//     dateStr.trim() &&
//     !loading &&
//     !noTables
//   )

//   return (
//     <div className="top-panel-shell">
//       <div className="top-panel-header">
//         <span className="top-panel-icon">⚙️</span>
//         <span>Configure Query</span>
//       </div>

//       <div className="top-panel-body">
//         {error && (
//           <div className="error-box" style={{ marginBottom: 12 }}>
//             ⚠ {error}
//           </div>
//         )}

//         {!returnInfo && (
//           <div className="ip-empty">
//             <div className="ip-empty-icon">🔎</div>
//             <div className="ip-empty-text">
//               Search for a return in the panel on the right to begin
//             </div>
//           </div>
//         )}

//         {returnInfo && step !== VARIANCE_STEPS.RESULT && (
//           <div className="ip-form">
//             <div className="ip-return-info">
//               <span className="ip-return-label">Return</span>
//               <span className="ip-return-name">
//                 {returnInfo.return_name}
//               </span>
//               <span className="vt-badge vt-badge-prev">
//                 {freqLabel(returnInfo.report_freq)}
//               </span>
//             </div>

//             <div className="ip-field">
//               <div className="field-label">Table</div>

//               {noTables ? (
//                 <div className="error-box" style={{ marginTop: 4 }}>
//                   No tables are available for this return.
//                 </div>
//               ) : (
//                 <div className="table-list ip-table-list">
//                   {tables.map((t) => (
//                     <div
//                       key={t.table_name}
//                       className={`table-item${tableName === t.table_name ? ' selected' : ''
//                         }`}
//                       onClick={() => setTableName(t.table_name)}
//                     >
//                       {t.table_name}
//                     </div>
//                   ))}
//                 </div>
//               )}
//             </div>

//             {/* Reporting Date */}
//             <div className="ip-field">
//               <div className="field-label">Reporting Date</div>

//               <div className="field-hint">
//                 e.g. <strong>{dateHint.example}</strong>
//               </div>

//               <div
//                 className="input-group flatpickr me-2 mb-2 mb-md-0"
//                 id="reportingDate"
//                 ref={flatpickrWrapRef}
//               >
//                 <input
//                   type="text"
//                   className="form-control bg-transparent"
//                   placeholder="Select Date"
//                   data-input=""
//                   readOnly
//                   disabled={loading}
//                 />

//                 <span className="input-group-text input-group-addon">
//                   <i data-lucide="calendar"></i>
//                 </span>
//               </div>
//             </div>

//             <div className="ip-field">
//               <div className="field-label">Comparison Periods</div>

//               <div className="period-chips">
//                 {[1, 2, 3].map((n) => (
//                   <button
//                     key={n}
//                     className={`period-chip${periods === n ? ' selected' : ''
//                       }`}
//                     onClick={() => setPeriods(n)}
//                   >
//                     {n} {n === 1 ? 'period' : 'periods'}
//                   </button>
//                 ))}
//               </div>
//             </div>

//             <div className="ip-actions">
//               <button
//                 className="btn"
//                 disabled={!canCompute}
//                 onClick={handleCompute}
//               >
//                 {loading ? (
//                   <>
//                     <span className="spinner" /> Computing…
//                   </>
//                 ) : (
//                   'Compute Variance'
//                 )}
//               </button>
//             </div>

//             {loading && (
//               <div className="loading-row">
//                 <span className="spinner" /> Querying Oracle and computing
//                 variance…
//               </div>
//             )}
//           </div>
//         )}

//         {returnInfo && step === VARIANCE_STEPS.RESULT && (
//           <div className="ip-success">
//             <div className="ip-success-icon">✅</div>

//             <div className="ip-success-text">
//               Variance computed successfully
//             </div>

//             <div className="ip-success-sub">
//               Results are shown in the table panel below
//             </div>

//             <div
//               className="ip-actions"
//               style={{ justifyContent: 'center', marginTop: 16 }}
//             >
//               <button
//                 className="btn btn-secondary"
//                 onClick={handleReset}
//               >
//                 ↺ New Query
//               </button>

//               <button
//                 className="btn"
//                 disabled={loading}
//                 onClick={handleCompute}
//               >
//                 {loading ? (
//                   <>
//                     <span className="spinner" /> Recomputing…
//                   </>
//                 ) : (
//                   '↻ Recompute'
//                 )}
//               </button>
//             </div>
//           </div>
//         )}
//       </div>
//     </div>
//   )
// }