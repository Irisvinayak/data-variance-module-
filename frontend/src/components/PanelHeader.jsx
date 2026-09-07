/**
 * PanelHeader — sticky header for bottom panels.
 * Shows Expand / Minimize when in normal state.
 * Shows a single Restore button when expanded or minimized.
 */
/**
 * `titleContent` replaces the plain `title` text with arbitrary content in the
 * same row — used by TablePanel to put the table name and its period badges in
 * the header rather than on a second line beneath it. `title` is still used
 * for the collapsed-panel label and the a11y name, so it must be supplied
 * either way.
 */
export default function PanelHeader({
  title, titleContent, icon, onExpand, onMinimize, isExpanded, isMinimized,
}) {
  const isNormal = !isExpanded && !isMinimized

  return (
    <div className={'ph-root' + (titleContent ? ' ph-root-rich' : '')}>
      <div className="ph-title">
        {icon && <span className="ph-icon">{icon}</span>}
        {titleContent ?? <span>{title}</span>}
        {isExpanded  && <span className="ph-state-badge">Expanded</span>}
        {isMinimized && <span className="ph-state-badge">Minimized</span>}
      </div>
      <div className="ph-controls">
        {isNormal && (
          <button
            className="ph-btn"
            onClick={onMinimize}
            title="Minimize panel"
            aria-label="Minimize panel"
          >
            −
          </button>
        )}
        {isNormal && (
          <button
            className="ph-btn"
            onClick={onExpand}
            title="Expand panel"
            aria-label="Expand panel"
          >
            □
          </button>
        )}
        {(isExpanded || isMinimized) && (
          <button
            className="ph-btn ph-btn-restore"
            onClick={isExpanded ? onExpand : onMinimize}
            title="Restore panel"
            aria-label="Restore panel"
          >
            ⊞ Restore
          </button>
        )}
      </div>
    </div>
  )
}
