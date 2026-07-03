import { useState, useEffect } from 'react'
import LayoutContainer from './components/LayoutContainer.jsx'
import { bootstrapAuthFromUrl } from './api.js'
import './layout.css'

function decodeJwtPayload(token) {
  try {
    const base64url = token.split('.')[1]
    if (!base64url) return null
    const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(decodeURIComponent(
      atob(base64).split('').map(c => '%' + c.charCodeAt(0).toString(16).padStart(2, '0')).join('')
    ))
  } catch { return null }
}

export default function App() {
  const [authReady, setAuthReady] = useState(false)
  const [loginId,   setLoginId]   = useState('')
  const [tenantId,  setTenantId]  = useState('')

  useEffect(() => {
    bootstrapAuthFromUrl()   // writes _at and _lid to sessionStorage

    const token   = sessionStorage.getItem('_at') ?? ''
    const payload = decodeJwtPayload(token)

    const lid = payload?.LoginId  ?? sessionStorage.getItem('_lid') ?? ''
    const tid = payload?.TenantId ?? ''

    console.log('[App] loginId resolved:', lid || 'EMPTY ❌')
    console.log('[App] tenantId resolved:', tid || 'EMPTY ❌')

    setLoginId(lid)
    setTenantId(tid)
    setAuthReady(true)
  }, [])

  if (!authReady) return (
    <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100vh' }}>
      Loading...
    </div>
  )

  return (
    <div className="app">
      <main className="app-main-full">
        <LayoutContainer loginId={loginId} uid={tenantId} />
      </main>
    </div>
  )
}