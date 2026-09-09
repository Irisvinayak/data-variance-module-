import { useEffect, useState } from 'react'
import LayoutContainer from './components/LayoutContainer.jsx'
import { bootstrapAuth } from './auth/index.js'
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

  useEffect(() => {
    let cancelled = false
    bootstrapAuth().then((resolved) => {
      if (!cancelled) setIdentity(resolved)
    })
    return () => { cancelled = true }
  }, [])

  if (!identity) {
    return (
      <div className="app">
        <main className="app-main-full">
          <div className="auth-booting">Starting…</div>
        </main>
      </div>
    )
  }

  return (
    <div className="app">
      <main className="app-main-full">
        <LayoutContainer
          loginId={identity.loginId}
          tenantId={identity.tenantId}
          uid={identity.uid}
        />
      </main>
    </div>
  )
}
