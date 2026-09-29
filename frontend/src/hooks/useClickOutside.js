import { useEffect, useRef } from 'react'

/**
 * Calls `onOutside` on a mousedown outside `ref`'s element, while `active`.
 *
 * The handler is read through a ref so callers can pass an inline function
 * without the document listener being torn down and re-added every render.
 */
export default function useClickOutside(ref, active, onOutside) {
  const handlerRef = useRef(onOutside)
  handlerRef.current = onOutside

  useEffect(() => {
    if (!active) return
    function handleMouseDown(e) {
      if (ref.current && !ref.current.contains(e.target)) handlerRef.current(e)
    }
    document.addEventListener('mousedown', handleMouseDown)
    return () => document.removeEventListener('mousedown', handleMouseDown)
  }, [ref, active])
}
