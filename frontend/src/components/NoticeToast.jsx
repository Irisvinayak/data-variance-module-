import { useEffect, useRef } from 'react'

/**
 * NoticeToast — small dismissible popup, bottom-right corner.
 * Used to surface non-fatal, easy-to-miss data conditions (e.g. "no
 * submission exists for one of the requested comparison periods") that
 * would otherwise just show up as a silently-empty column in the result
 * table, with nothing telling the user WHY it's empty.
 */
export default function NoticeToast({ message, onDismiss, autoDismissMs = 8000 }) {
  // Callers pass an inline onDismiss, which is a new function every render.
  // Keyed on it, the timer restarted on each parent re-render and the toast
  // never auto-dismissed while the user was interacting with the page.
  const onDismissRef = useRef(onDismiss)
  onDismissRef.current = onDismiss

  useEffect(() => {
    if (!message || !autoDismissMs) return
    const timer = setTimeout(() => onDismissRef.current(), autoDismissMs)
    return () => clearTimeout(timer)
  }, [message, autoDismissMs])

  if (!message) return null

  return (
    <div className="notice-toast" role="status">
      <span className="notice-toast-icon">&#9888;</span>
      <span className="notice-toast-message">{message}</span>
      <button
        className="notice-toast-close"
        onClick={onDismiss}
        aria-label="Dismiss notice"
        title="Dismiss"
      >
        &times;
      </button>
    </div>
  )
}
