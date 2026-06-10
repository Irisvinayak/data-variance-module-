import LayoutContainer from './components/LayoutContainer.jsx'
import './layout.css'

export default function App() {
  // ── Read query params passed by .NET iframe URL ────────────────────────────
  // .NET cshtml builds:  http://localhost:3001?loginId=iris810&uid=104&aspSession=xyz
  const params  = new URLSearchParams(window.location.search)
  const loginId = params.get('loginId') || ''
  const uid     = params.get('uid')     || ''

  return (
    <div className="app">
      <main className="app-main-full">
        <LayoutContainer loginId={loginId} uid={uid} />
      </main>
    </div>
  )
}