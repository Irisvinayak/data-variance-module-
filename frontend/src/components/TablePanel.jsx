/**
 * TablePanel — top analysis panel. Renders the DataVarianceBlock result.
 * Only mounted when a result exists (LayoutContainer conditionally renders the bottom section).
 */
import PanelHeader from './PanelHeader.jsx'
import ColumnVisibilityMenu from './ColumnVisibilityMenu.jsx'
import { DataVarianceBlock, VarianceHeaderMeta } from '../DataVarianceTable.jsx'

// Still stateless — column visibility is owned by LayoutContainer because the
// visualization panel is a sibling that has to honour the same hidden set.
export default function TablePanel({
  result, tableState, onExpand, onMinimize, onReset, loading,
  hiddenCols, onToggleCol, onHideCol, onShowAllCols, onHideAllCols,
}) {
  const isExpanded  = tableState === 'expanded'
  const isMinimized = tableState === 'minimized'

  // display_columns is every displayable column (values + info); `columns` is
  // the comparable/numeric subset, used here only to tag which is which.
  const allCols       = result?.display_columns ?? result?.columns ?? []
  const comparableSet = new Set((result?.columns ?? []).map((c) => c.toUpperCase()))

  return (
    <div className={`bottom-panel-shell${isMinimized ? ' panel-shell-collapsed' : ''}`}>
      <PanelHeader
        title="Table Output"
        /* The static "TABLE OUTPUT" label is replaced by what the panel is
           actually showing — table name + period badges — so the row that used
           to carry only a redundant label now carries the information, and the
           separate meta row below it goes away entirely. Falls back to the
           plain title while there is no result yet (initial load / recompute)
           and whenever the panel is collapsed, where the badges would not
           fit. */
        titleContent={
          result && !isMinimized ? (
            <>
              <VarianceHeaderMeta result={result} />
              <ColumnVisibilityMenu
                columns={allCols}
                comparableSet={comparableSet}
                hidden={hiddenCols}
                onToggle={onToggleCol}
                onShowAll={onShowAllCols}
                onHideAll={onHideAllCols}
              />
            </>
          ) : null
        }
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
              <DataVarianceBlock
                result={result}
                showHeader={false}
                hiddenCols={hiddenCols}
                onHideCol={onHideCol}
              />
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
