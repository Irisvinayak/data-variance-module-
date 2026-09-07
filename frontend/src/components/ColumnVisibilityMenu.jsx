/**
 * ColumnVisibilityMenu — checkbox dropdown for hiding/showing table columns.
 *
 * Deliberately a structural copy of ControlBar.jsx's DateField (open state +
 * click-outside ref effect + the same .ctrl-date-* class names), so it reuses
 * that component's stylesheet wholesale and reads as a control the user has
 * already met elsewhere in this toolbar.
 *
 * Visibility is a pure view filter applied client-side — it never re-queries.
 * See DataVarianceBlock's allDisplayCols filter for where it lands.
 */
import { useEffect, useRef, useState } from 'react'

// Above this many columns a flat checklist stops being scannable and the
// search box earns its space; below it, the box is just clutter.
const SEARCH_THRESHOLD = 12

export default function ColumnVisibilityMenu({
  columns = [],
  comparableSet,
  hidden = [],
  onToggle,
  onShowAll,
  onHideAll,
}) {
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('')
  const wrapRef = useRef(null)

  useEffect(() => {
    function handleClickOutside(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    if (open) document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [open])

  if (!columns.length) return null

  const hiddenSet   = new Set(hidden.map((c) => c.toUpperCase()))
  const visibleCols = columns.filter((c) => !hiddenSet.has(c.toUpperCase()))
  const hiddenCount = columns.length - visibleCols.length

  const showSearch = columns.length > SEARCH_THRESHOLD
  const listed = filter.trim()
    ? columns.filter((c) => c.toLowerCase().includes(filter.trim().toLowerCase()))
    : columns

  return (
    <div className="ctrl-date-wrap col-vis-wrap" ref={wrapRef}>
      {/* The visible/total count is load-bearing, not decoration: a trimmed
          table that someone reads numbers off or screenshots is the real
          failure mode of this feature, and it is the only thing on screen that
          says the view is filtered. Doubly so because the hidden set is
          restored from localStorage across sessions. */}
      <button
        type="button"
        className={'ctrl-select ctrl-input-date' + (hiddenCount ? ' ctrl-input-date-on' : '')}
        onClick={() => setOpen((o) => !o)}
        title={
          hiddenCount
            ? `${hiddenCount} column${hiddenCount === 1 ? '' : 's'} hidden — click to change`
            : 'Show or hide columns'
        }
      >
        <span className="ctrl-date-summary">
          Columns {visibleCols.length}/{columns.length}
        </span>
        <span className="ctrl-date-arrow">{open ? '\u25B2' : '\u25BC'}</span>
      </button>

      {open && (
        <div className="ctrl-date-menu">
          <div className="ctrl-date-menu-head">
            <span>{hiddenCount ? `${hiddenCount} hidden` : 'All shown'}</span>
            <span className="col-vis-head-actions">
              <button
                type="button"
                className="ctrl-date-clear"
                onClick={onShowAll}
                disabled={hiddenCount === 0}
              >
                Show all
              </button>
              <button
                type="button"
                className="ctrl-date-clear"
                onClick={onHideAll}
                disabled={visibleCols.length === 0}
              >
                Hide all
              </button>
            </span>
          </div>

          {showSearch && (
            <input
              className="disambig-search-input col-vis-search"
              placeholder="Filter columns…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              autoFocus
            />
          )}

          <div className="ctrl-date-list">
            {listed.length === 0 ? (
              <div className="ctrl-date-note">No columns match “{filter}”.</div>
            ) : (
              /* Rendered in the backend's own column order — NOT sorted — so
                 the list reads top-to-bottom the way the table reads
                 left-to-right. */
              listed.map((col) => {
                const visible = !hiddenSet.has(col.toUpperCase())
                // Deliberately NOT reusing .ctrl-date-item-on here: in the date
                // picker "on" marks the few chosen entries, but here almost
                // every row is visible by default, so highlighting them all
                // would just be noise. The HIDDEN ones are the exception worth
                // marking, so they dim instead.
                return (
                  <label
                    key={col}
                    className={'ctrl-date-item' + (visible ? '' : ' col-vis-item-off')}
                  >
                    <input
                      type="checkbox"
                      checked={visible}
                      onChange={() => onToggle(col)}
                    />
                    <span className="col-vis-name">{col}</span>
                    {/* Info columns carry no variance metrics — worth marking,
                        since hiding one has a different consequence (it also
                        leaves the chart untouched) than hiding a value column. */}
                    {comparableSet && !comparableSet.has(col.toUpperCase()) && (
                      <span className="col-vis-tag">info</span>
                    )}
                  </label>
                )
              })
            )}
          </div>
        </div>
      )}
    </div>
  )
}
