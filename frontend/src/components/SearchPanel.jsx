// /**
//  * SearchPanel — top-right panel. Searches for a return by name and shows found metadata.
//  */
// import { freqLabel } from '../types.js'

// export default function SearchPanel({ returnName, setReturnName, returnInfo, loading, handleFindReturn }) {
//   return (
//     <div className="top-panel-shell">
//       <div className="top-panel-header">
//         <span className="top-panel-icon">🔍</span>
//         <span>Search Returns</span>
//       </div>

//       <div className="top-panel-body">
//         <div className="field-label">Return / Report Name</div>
//         <div className="field-hint" style={{ marginBottom: 10 }}>
//           Enter the return code or name (e.g. CIMS_RAQ, BSR1)
//         </div>

//         <div className="input-row">
//           <input
//             className="text-input"
//             value={returnName}
//             onChange={(e) => setReturnName(e.target.value)}
//             onKeyDown={(e) => e.key === 'Enter' && !loading && handleFindReturn()}
//             placeholder="e.g. CIMS_RAQ"
//           />
//           <button
//             className="btn"
//             disabled={!returnName.trim() || loading}
//             onClick={handleFindReturn}
//           >
//             {loading ? 'Searching…' : 'Search →'}
//           </button>
//         </div>

//         {loading && (
//           <div className="loading-row" style={{ marginTop: 10 }}>
//             <span className="spinner" /> Looking up return…
//           </div>
//         )}

//         {returnInfo && (
//           <div className="sp-result">
//             <div className="sp-result-name">{returnInfo.return_name}</div>
//             <div className="sp-result-badges">
//               <span className="vt-badge vt-badge-curr">{freqLabel(returnInfo.report_freq)}</span>
//               <span className="vt-badge vt-badge-prev">
//                 {(returnInfo.tables || []).length} table{(returnInfo.tables || []).length !== 1 ? 's' : ''}
//               </span>
//             </div>
//             {returnInfo.return_id && (
//               <div className="sp-result-id">Return ID: {returnInfo.return_id}</div>
//             )}
//           </div>
//         )}

//         {!returnInfo && !loading && (
//           <div className="sp-empty">
//             <div className="sp-empty-icon">📂</div>
//             <div className="sp-empty-text">No return selected yet</div>
//           </div>
//         )}
//       </div>
//     </div>
//   )
// }
