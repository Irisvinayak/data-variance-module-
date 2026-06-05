/**
 * PanelHeader — sticky header for bottom panels.
 * Shows Expand / Minimize when in normal state.
 * Shows a single Restore button when expanded or minimized.
 */
export default function PanelHeader({ title, icon, onExpand, onMinimize, isExpanded, isMinimized }) {
  const isNormal = !isExpanded && !isMinimized

  return (
    <div className="ph-root">
      <div className="ph-title">
        {icon && <span className="ph-icon">{icon}</span>}
        <span>{title}</span>
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
