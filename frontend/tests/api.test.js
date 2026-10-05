// @vitest-environment jsdom
// H-21: request timeouts / abort handling in src/api.js (fetch is faked).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

async function load() {
  vi.resetModules()
  const token = await import('../src/auth/token.js')
  const auth = await import('../src/auth/index.js')
  const api = await import('../src/api.js')
  return { token, auth, api }
}

// A fetch that never answers on its own but honours the abort signal.
function hangingFetch() {
  const calls = []
  const fn = vi.fn((url, opts) => new Promise((_, reject) => {
    calls.push({ url, opts })
    const abort = () => {
      const e = new Error('aborted')
      e.name = 'AbortError'
      reject(e)
    }
    // Like real fetch: an already-aborted signal rejects straight away.
    if (opts.signal.aborted) abort()
    else opts.signal.addEventListener('abort', abort)
  }))
  fn.calls = calls
  return fn
}

const jsonRes = (body, status = 200) => ({
  ok: status < 400, status, json: async () => body,
})

const freshJwt = () =>
  'h.' + btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 600 })) + '.s'

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('H-21 request timeouts', () => {
  it('a successful request resolves and clears its timer', async () => {
    const { api } = await load()
    vi.stubGlobal('fetch', vi.fn(async () => jsonRes({ allowed_forms: ['1'] })))
    const out = await api.getMyReturns()
    expect(out.allowed_forms).toEqual(['1'])
    expect(vi.getTimerCount()).toBe(0)
  })

  it('a slow request is aborted and reported as a timeout', async () => {
    const { api } = await load()
    const f = hangingFetch()
    vi.stubGlobal('fetch', f)
    const p = api.findReturnTables('X').catch((e) => e)
    await vi.advanceTimersByTimeAsync(120_000)
    const err = await p
    expect(err).toBeInstanceOf(Error)
    expect(err.message).toMatch(/timed out/i)
    expect(f.calls[0].opts.signal.aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not time out early', async () => {
    const { api } = await load()
    const f = hangingFetch()
    vi.stubGlobal('fetch', f)
    api.findReturnTables('X').catch(() => {})
    await vi.advanceTimersByTimeAsync(119_000)
    expect(f.calls[0].opts.signal.aborted).toBe(false)
    await vi.advanceTimersByTimeAsync(2_000)
    expect(f.calls[0].opts.signal.aborted).toBe(true)
  })

  it('NL resolve has the longer 300 s budget', async () => {
    const { api } = await load()
    const f = hangingFetch()
    vi.stubGlobal('fetch', f)
    api.resolveNlQuery('total loans').catch(() => {})
    await vi.advanceTimersByTimeAsync(200_000)
    expect(f.calls[0].opts.signal.aborted).toBe(false)
    await vi.advanceTimersByTimeAsync(101_000)
    expect(f.calls[0].opts.signal.aborted).toBe(true)
  })

  it('a caller abort is rethrown as AbortError, not reported as a timeout', async () => {
    const { api } = await load()
    vi.stubGlobal('fetch', hangingFetch())
    const ctl = new AbortController()
    const p = api.resolveNlQuery('q', { signal: ctl.signal }).catch((e) => e)
    ctl.abort()
    const err = await p
    expect(err.name).toBe('AbortError')
    expect(err.message).not.toMatch(/timed out/i)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('an already-aborted caller signal aborts immediately', async () => {
    const { api } = await load()
    vi.stubGlobal('fetch', hangingFetch())
    const ctl = new AbortController()
    ctl.abort()
    const err = await api.resolveNlQuery('q', { signal: ctl.signal }).catch((e) => e)
    expect(err.name).toBe('AbortError')
  })

  it('the caller-signal listener is removed after the request settles', async () => {
    const { api } = await load()
    vi.stubGlobal('fetch', vi.fn(async () => jsonRes({ ok: 1 })))
    const ctl = new AbortController()
    const add = vi.spyOn(ctl.signal, 'addEventListener')
    const rem = vi.spyOn(ctl.signal, 'removeEventListener')
    await api.resolveNlQuery('q', { signal: ctl.signal })
    expect(add).toHaveBeenCalledWith('abort', expect.any(Function), { once: true })
    expect(rem).toHaveBeenCalledWith('abort', add.mock.calls[0][1])
  })

  it('timeoutMs / signal are not forwarded to fetch as caller options', async () => {
    const { api } = await load()
    const f = vi.fn(async () => jsonRes({}))
    vi.stubGlobal('fetch', f)
    await api.resolveNlQuery('q')
    const opts = f.mock.calls[0][1]
    expect('timeoutMs' in opts).toBe(false)
    expect(opts.signal).toBeInstanceOf(AbortSignal)
  })

  it('an old timed-out request cannot affect a newer one', async () => {
    const { api } = await load()
    const f = hangingFetch()
    vi.stubGlobal('fetch', f)
    const a = api.findReturnTables('A').catch((e) => e)
    await vi.advanceTimersByTimeAsync(60_000)
    const b = api.findReturnTables('B').catch((e) => e)
    await vi.advanceTimersByTimeAsync(60_000)          // A times out now
    expect((await a).message).toMatch(/timed out/i)
    expect(f.calls[1].opts.signal.aborted).toBe(false) // B untouched
    await vi.advanceTimersByTimeAsync(60_000)
    expect((await b).message).toMatch(/timed out/i)
  })

  it('error bodies: JSON detail, validation list, and non-JSON fall back sensibly', async () => {
    const { api } = await load()
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 403, json: async () => ({ detail: 'denied' }) })))
    await expect(api.getMyReturns()).rejects.toThrow('denied')
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 422, json: async () => ({ detail: [{ msg: 'bad' }] }) })))
    await expect(api.getMyReturns()).rejects.toThrow('bad')
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 502, json: async () => { throw new Error('html') } })))
    await expect(api.getMyReturns()).rejects.toThrow('Auth error (502)')
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => { throw new Error('html') } })))
    await expect(api.getMyReturns()).rejects.toThrow(/non-JSON/)
  })
})

describe('request headers (H-09 client side)', () => {
  it('sends Authorization only when a token is held; the token is never in the URL', async () => {
    const { api, token } = await load()
    const f = vi.fn(async () => jsonRes({}))
    vi.stubGlobal('fetch', f)
    await api.getMyReturns()
    expect(f.mock.calls[0][1].headers).toBeUndefined()

    const jwt = freshJwt()
    expect(token.setToken(jwt)).toBe(true)
    await api.getMyReturns()
    const [url, opts] = f.mock.calls[1]
    expect(opts.headers.get('Authorization')).toBe(`Bearer ${jwt}`)
    expect(url).not.toContain(jwt)
    expect(url).not.toMatch(/jwt|token|_at=/i)
  })

  it('a 401 with a token drops it and asks the host for a fresh one (recovery)', async () => {
    const { api, token } = await load()
    token.setToken(freshJwt())
    const post = vi.fn()
    const parentSpy = vi.spyOn(window, 'parent', 'get').mockReturnValue({ postMessage: post })
    vi.stubGlobal('fetch', vi.fn(async () => jsonRes({ detail: 'Invalid or expired credentials.' }, 401)))
    await expect(api.getMyReturns()).rejects.toThrow()
    expect(token.getToken()).toBeNull()
    expect(post).toHaveBeenCalledWith({ type: 'DATAVAR_READY' }, expect.any(String))
    parentSpy.mockRestore()
  })
})
