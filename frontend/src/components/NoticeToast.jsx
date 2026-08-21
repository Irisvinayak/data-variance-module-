import { useEffect } from 'react'

/**
 * NoticeToast — small dismissible popup, bottom-right corner.
 * Used to surface non-fatal, easy-to-miss data conditions (e.g. "no
 * submission exists for one of the requested comparison periods") that
 * would otherwise just show up as a silently-empty column in the result
 * table, with nothing telling the user WHY it's empty.
 */
export default function NoticeToast({ message, onDismiss, autoDismissMs = 8000 }) {
  useEffect(() => {
    if (!autoDismissMs) return
    const timer = setTimeout(onDismiss, autoDismissMs)
    return () => clearTimeout(timer)
  }, [message, autoDismissMs, onDismiss])

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
