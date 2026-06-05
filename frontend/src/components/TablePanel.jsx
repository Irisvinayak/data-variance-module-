/**
 * TablePanel — top analysis panel. Renders the DataVarianceBlock result.
 * Only mounted when a result exists (LayoutContainer conditionally renders the bottom section).
 */
import PanelHeader from './PanelHeader.jsx'
import { DataVarianceBlock } from '../DataVarianceTable.jsx'

export default function TablePanel({ result, tableState, onExpand, onMinimize, onReset, loading }) {
  const isExpanded  = tableState === 'expanded'
  const isMinimized = tableState === 'minimized'

  return (
    <div className={`bottom-panel-shell${isMinimized ? ' panel-shell-collapsed' : ''}`}>
      <PanelHeader
        title="Table Output"
        icon="📊"
        onExpand={onExpand}
        onMinimize={onMinimize}
        isExpanded={isExpanded}
        isMinimized={isMinimized}
      />

      {isMinimized ? (
        <div className="panel-collapsed-body">
          <span className="panel-collapsed-label">Table Output</span>
        </div>
      ) : (
        <div className="bottom-panel-body">
          {loading && (
            <div className="bottom-panel-loading">
              <span className="spinner" />&thinsp;Recomputing variance…
            </div>
          )}

          {result && !loading && (
            <>
              <DataVarianceBlock result={result} />
              <div className="row-end" style={{ marginTop: 16, paddingBottom: 8 }}>
                <button className="btn btn-secondary btn-sm" onClick={onReset}>
                  ↺ New Query
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
