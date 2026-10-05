// @vitest-environment jsdom
// H-09 (client side): token memory-only, postMessage origin checks, expiry,
// recovery handshake; M-11: no identity/config logging outside dev builds;
// M-34: bootstrapAuth behaviour.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const HOST = 'https://ideal60.example.com'

const b64 = (o) => btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
const jwt = (claims) => `h.${b64(claims)}.s`
const nowS = () => Math.floor(Date.now() / 1000)
const good = (extra = {}) => jwt({ LoginId: 'alice@bank', TenantId: '1001', exp: nowS() + 600, ...extra })

async function load({ hostOrigin = HOST, version = '6.0', dev = true } = {}) {
  vi.resetModules()
  vi.stubEnv('VITE_HOST_ORIGIN', hostOrigin)
  vi.stubEnv('DEV', dev)
  vi.stubEnv('VITE_APP_VERSION', version ?? '')
  const token = await import('../src/auth/token.js')
  const auth = await import('../src/auth/index.js')
  return { token, auth }
}

function sendFromHost(data, { origin = HOST, source = window } = {}) {
  window.dispatchEvent(new MessageEvent('message', { data, origin, source }))
}

beforeEach(() => {
  sessionStorage.clear()
  window.history.replaceState({}, '', '/')
  vi.spyOn(window, 'parent', 'get').mockReturnValue(window) // top-level by default
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('H-09 postMessage handshake', () => {
  it('accepts DATAVAR_AUTH from the expected origin and keeps the token in memory only', async () => {
    const { token, auth } = await load()
    await auth.bootstrapAuth()
    const t = good()
    sendFromHost({ type: 'DATAVAR_AUTH', jwt: t, tenantId: '1001' })
    expect(token.getToken()).toBe(t)
    expect(auth.hasToken()).toBe(true)
    // never persisted, never in the URL
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(t)
    expect(JSON.stringify({ ...localStorage })).not.toContain(t)
    expect(window.location.href).not.toContain(t)
  })

  it('rejects a message from an unexpected origin', async () => {
    const { token, auth } = await load()
    await auth.bootstrapAuth()
    sendFromHost({ type: 'DATAVAR_AUTH', jwt: good() }, { origin: 'https://evil.example.com' })
    expect(token.getToken()).toBeNull()
  })

  it('rejects a message that does not come from the parent window', async () => {
    const { token, auth } = await load()
    await auth.bootstrapAuth()
    sendFromHost({ type: 'DATAVAR_AUTH', jwt: good() }, { source: {} })
    expect(token.getToken()).toBeNull()
  })

  it('rejects an expired token', async () => {
    const { token, auth } = await load()
    await auth.bootstrapAuth()
    sendFromHost({ type: 'DATAVAR_AUTH', jwt: good({ exp: nowS() - 3600 }) })
    expect(token.getToken()).toBeNull()
  })

  it('rejects malformed payloads (wrong type, non-string, empty jwt)', async () => {
    const { token, auth } = await load()
    await auth.bootstrapAuth()
    for (const data of [null, 'x', { type: 'OTHER', jwt: good() }, { type: 'DATAVAR_AUTH', jwt: 123 },
      { type: 'DATAVAR_AUTH', jwt: '   ' }, { type: 'DATAVAR_AUTH' }]) {
      sendFromHost(data)
    }
    expect(token.getToken()).toBeNull()
  })

  it('a token that expires later is discarded on next read', async () => {
    vi.useFakeTimers()
    const { token } = await load()
    expect(token.setToken(good({ exp: nowS() + 60 }))).toBe(true)
    vi.advanceTimersByTime(60_000 + 31_000)       // past exp + 30 s leeway
    expect(token.getToken()).toBeNull()
    vi.useRealTimers()
  })

  it('a fresh handshake replaces an expired token (recovery)', async () => {
    const { token, auth } = await load()
    await auth.bootstrapAuth()
    const post = vi.fn()
    window.parent.postMessage = post
    token.setToken(good())
    token.clearToken()                              // what api.js does after a 401
    auth.postReady()
    const fresh = good({ exp: nowS() + 900 })
    sendFromHost({ type: 'DATAVAR_AUTH', jwt: fresh })
    expect(token.getToken()).toBe(fresh)
  })

  it('DATAVAR_READY is posted to the configured host origin, never "*"', async () => {
    const { auth } = await load()
    const post = vi.fn()
    vi.spyOn(window, 'parent', 'get').mockReturnValue({ postMessage: post })
    auth.postReady()
    expect(post).toHaveBeenCalledWith({ type: 'DATAVAR_READY' }, HOST)
  })

  it('handshake listener is registered once', async () => {
    const { auth } = await load()
    const add = vi.spyOn(window, 'addEventListener')
    await auth.bootstrapAuth()
    await auth.bootstrapAuth()
    expect(add.mock.calls.filter((c) => c[0] === 'message')).toHaveLength(1)
  })

  it('a legacy ?_at= URL token is moved to memory and stripped from the address bar', async () => {
    window.history.replaceState({}, '', `/?_at=${good()}&_lid=alice&_tid=1001`)
    const { token, auth } = await load()
    await auth.bootstrapAuth()
    expect(window.location.search).not.toContain('_at=')
    expect(token.getToken()).not.toBeNull()
    expect(sessionStorage.getItem('_at')).toBeNull()
  })

  it('any token left in sessionStorage by an older build is removed at startup', async () => {
    sessionStorage.setItem('_at', good())
    const { auth } = await load()
    await auth.bootstrapAuth()
    expect(sessionStorage.getItem('_at')).toBeNull()
  })

  it('identity comes from the token claims; X-Tenant-Id value matches', async () => {
    const { auth } = await load()
    await auth.bootstrapAuth()
    const changes = []
    auth.onIdentityChange((i) => changes.push(i))
    sendFromHost({ type: 'DATAVAR_AUTH', jwt: good(), tenantId: '1001' })
    expect(auth.currentTenantId()).toBe('1001')
    expect(changes.at(-1).loginId).toBe('alice@bank')
  })
})

describe('M-11 no identity or configuration in the console outside dev builds', () => {
  it('production mode: bootstrap with identity in the URL logs nothing', async () => {
    window.history.replaceState({}, '', '/?loginId=alice@bank&tenantId=1001')
    const spies = ['log', 'warn', 'error', 'info', 'debug'].map((m) => vi.spyOn(console, m).mockImplementation(() => {}))
    const { auth } = await load({ dev: false, version: '6.0' })
    await auth.bootstrapAuth()
    // 6.0 with loginId and no tenant would warn (with the id) in dev; also fail /app-config path
    window.history.replaceState({}, '', '/?loginId=bob@bank')
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('down') }))
    const m2 = await load({ dev: false, version: '' })
    await m2.auth.bootstrapAuth()
    for (const s of spies) expect(s).not.toHaveBeenCalled()
  })

  it('development mode: the diagnostic still appears (so the guard is what silences prod)', async () => {
    window.history.replaceState({}, '', '/?loginId=bob@bank')
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { auth } = await load({ dev: true, version: '6.0' })
    await auth.bootstrapAuth()
    expect(warn).toHaveBeenCalled()
  })
})

describe('M-34 bootstrapAuth', () => {
  it('resolves with the 5.5 identity from ?loginId= and scrubs session credentials', async () => {
    window.history.replaceState({}, '', '/?loginId=iris810&aspSession=SECRET&uid=7')
    const { auth } = await load({ version: '5.5' })
    const id = await auth.bootstrapAuth()
    expect(id).toMatchObject({ loginId: 'iris810', tenantId: '', version: '5.5', ready: true })
    expect(window.location.search).toBe('?loginId=iris810')
  })

  it('/app-config that never answers is cut off after 15 s and bootstrap still completes', async () => {
    vi.useFakeTimers()
    const f = vi.fn((url, opts) => new Promise((_, rej) => {
      opts.signal.addEventListener('abort', () => rej(Object.assign(new Error('aborted'), { name: 'AbortError' })))
    }))
    vi.stubGlobal('fetch', f)
    const { auth } = await load({ version: '' })
    window.history.replaceState({}, '', '/?loginId=iris810')
    const p = auth.bootstrapAuth()
    await vi.advanceTimersByTimeAsync(14_000)
    let done = false
    p.then(() => { done = true })
    await vi.advanceTimersByTimeAsync(0)
    expect(done).toBe(false)
    await vi.advanceTimersByTimeAsync(1_500)
    const id = await p
    expect(f).toHaveBeenCalledTimes(1)               // no retry loop
    expect(id.ready).toBe(true)
    expect(id.version).toBe('5.5')                   // guessed from the URL
    vi.useRealTimers()
  })

  it('uses the backend version from /app-config', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ version: '6.0' }) })))
    const { auth } = await load({ version: '' })
    const id = await auth.bootstrapAuth()
    expect(id.version).toBe('6.0')
  })
})

describe('L-12 orphaned-login cleanup', () => {
  async function resolveWith(search, store) {
    vi.resetModules()
    vi.stubEnv('DEV', false)
    window.history.replaceState({}, '', `/${search}`)
    sessionStorage.clear()
    for (const [k, v] of Object.entries(store)) sessionStorage.setItem(k, v)
    const s60 = await import('../src/auth/strategy60.js')
    s60.resolve()
  }

  it('an unrelated query parameter does not stop the stale login from being cleared', async () => {
    await resolveWith('?lang=en', { _lid: 'stale@bank' })      // a login with no tenant beside it
    expect(sessionStorage.getItem('_lid')).toBeNull()
  })

  it('an identity parameter in the URL still protects the stored values', async () => {
    await resolveWith('?loginId=real@bank', { _lid: 'stale@bank' })
    expect(sessionStorage.getItem('_lid')).not.toBeNull()
  })
})
