import { useEffect, useState } from 'react'
import LayoutContainer from './components/LayoutContainer.jsx'
import { bootstrapAuth, onIdentityChange } from './auth/index.js'
import './layout.css'

/**
 * Resolves the caller's identity before rendering anything.
 *
 * Which iDEAL host supplied that identity — 5.5 via ?loginId=, 6.0 via a JWT
 * in ?_at= — is decided by src/auth/ from the backend's VERSION, so this
 * component is the same for both. Rendering is gated on the resolution
 * because api.js reads the identity synchronously: mounting LayoutContainer
 * first would fire its /auth/my-returns effect with an empty loginId and show
 * a spurious "not authorised" state.
 */
export default function App() {
  const [identity, setIdentity] = useState(null)
  const [bootError, setBootError] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    setBootError(false)
    bootstrapAuth()
      .then((resolved) => {
        if (!cancelled) setIdentity(resolved)
      })
      .catch((err) => {
        if (import.meta.env.DEV) console.error('[auth] Identity bootstrap failed:', err)
        if (!cancelled) setBootError(true)
      })
    // A host token can arrive after startup (postMessage handshake).
    const off = onIdentityChange((next) => { if (!cancelled) setIdentity(next) })
    return () => { cancelled = true; off() }
  }, [attempt])

  if (bootError) {
    return (
      <div className="app">
        <main className="app-main-full">
          <div className="auth-booting">
            <p>Could not start the application. Please check your connection and try again.</p>
            <button type="button" onClick={() => setAttempt((n) => n + 1)}>Retry</button>
          </div>
        </main>
      </div>
    )
  }

  if (!identity) {
    return (
      <div className="app">
        <main className="app-main-full">
          <div className="auth-booting">Starting…</div>
        </main>
      </div>
    )
  }

  // No "not signed in" gate here on purpose: a backend running with auth
  // disabled legitimately works without a loginId, so blocking the whole app on
  // an empty one would break that. When auth IS enforced, the API's own 401
  // reaches the UI's normal error display.

  return (
    <div className="app">
      <main className="app-main-full">
        <LayoutContainer loginId={identity.loginId} />
      </main>
    </div>
  )
}
