import { useCallback, useEffect, useId, useState } from 'react'

/**
 * Keyboard pattern for a filterable listbox whose focus stays in the filter input
 * (the WAI-ARIA "combobox with listbox popup", active-descendant variant; code review M-45).
 *
 *   ArrowDown / ArrowUp  move the highlighted option (wraps around)
 *   Home / End           first / last option
 *   Enter                choose the highlighted option
 *   Escape               left to the caller (it closes the popup)
 *
 * Focus never leaves the input: the highlighted option is announced through
 * aria-activedescendant, so typing to filter keeps working while the arrows move.
 * Mouse and touch are untouched: options stay ordinary buttons with onClick.
 *
 * `items` is the CURRENT (already filtered) list; `onChoose(item)` runs on Enter.
 */
export default function useListboxNav(items, onChoose) {
  const base = useId()
  const [active, setActive] = useState(items.length ? 0 : -1)
  const count = items.length

  // The list changed (the user typed): start again from the first match.
  useEffect(() => {
    setActive(count ? 0 : -1)
  }, [count])

  const optionId = useCallback((i) => `${base}-opt-${i}`, [base])

  // Keep the highlighted option visible in a scrolled list.
  useEffect(() => {
    if (active < 0) return
    const el = document.getElementById(optionId(active))
    if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest' })
  }, [active, optionId])

  const onKeyDown = useCallback((e) => {
    if (!count) return
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setActive((a) => (a + 1) % count)
        break
      case 'ArrowUp':
        e.preventDefault()
        setActive((a) => (a <= 0 ? count - 1 : a - 1))
        break
      case 'Home':
        e.preventDefault()
        setActive(0)
        break
      case 'End':
        e.preventDefault()
        setActive(count - 1)
        break
      case 'Enter':
        if (active >= 0 && active < count) {
          e.preventDefault()
          onChoose(items[active])
        }
        break
      default:
    }
  }, [count, active, items, onChoose])

  /** Props for the filter input. */
  const inputProps = {
    role: 'combobox',
    'aria-autocomplete': 'list',
    'aria-expanded': true,
    'aria-controls': `${base}-list`,
    'aria-activedescendant': active >= 0 && active < count ? optionId(active) : undefined,
    onKeyDown,
  }

  /** Props for the listbox element. */
  const listProps = { id: `${base}-list`, role: 'listbox' }

  /** Props for option number `i`. */
  const optionProps = (i) => ({
    id: optionId(i),
    role: 'option',
    'aria-selected': i === active,
    tabIndex: -1,
    onMouseEnter: () => setActive(i),
  })

  return { active, inputProps, listProps, optionProps }
}
