// @vitest-environment jsdom
// H-10 build-time CSP handling; M-02 legacy ?_at= URL token switch.
import fs from 'node:fs'
import path from 'node:path'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { applyCsp, parseHostOrigins } from '../csp.js'

const pub = (f) => fs.readFileSync(path.resolve(__dirname, '../public', f), 'utf8')

describe('H-10 applyCsp', () => {
  for (const file of ['web.config', 'web.60.config']) {
    const xml = pub(file)

    it(`${file}: unchanged when nothing is configured`, () => {
      expect(applyCsp(xml)).toBe(xml)
      expect(applyCsp(xml, { hostOrigin: '', enforce: '' })).toBe(xml)
    })

    it(`${file}: host origin narrows frame-ancestors, header stays report-only`, () => {
      const out = applyCsp(xml, { hostOrigin: 'https://ideal.example.com, https://b.example.com:8443' })
      expect(out).toContain('frame-ancestors https://ideal.example.com https://b.example.com:8443')
      expect(out).not.toContain('frame-ancestors *')
      expect(out).toContain('Content-Security-Policy-Report-Only')
    })

    it(`${file}: enforce renames the header and keeps the rest of the policy`, () => {
      const out = applyCsp(xml, { hostOrigin: 'https://ideal.example.com', enforce: 'true' })
      expect(out).toContain('name="Content-Security-Policy"')
      expect(out).not.toContain('Report-Only')
      expect(out).toContain("default-src 'self'")
      expect(out).toContain("script-src 'self'")
    })

    it(`${file}: enforcing without a host origin is refused`, () => {
      expect(() => applyCsp(xml, { enforce: 'true' })).toThrow(/VITE_HOST_ORIGIN/)
    })
  }

  it('rejects things that are not origins', () => {
    for (const bad of ['ideal.example.com', 'javascript:alert(1)', 'https://x.com/path', "https://x.com; script-src *"]) {
      expect(() => parseHostOrigins(bad)).toThrow()
    }
  })

  it('a policy it cannot narrow is an error, not a silent no-op', () => {
    expect(() => applyCsp('<x/>', { hostOrigin: 'https://a.example.com' })).toThrow()
  })
})

describe('M-02 URL token switch', () => {
  const b64 = (o) => btoa(JSON.stringify(o)).replace(/=+$/, '')
  const tok = `h.${b64({ LoginId: 'a', TenantId: '1', exp: Math.floor(Date.now() / 1000) + 600 })}.s`

  async function run(flag) {
    vi.resetModules()
    if (flag !== undefined) vi.stubEnv('VITE_ALLOW_URL_TOKEN', flag)
    window.history.replaceState({}, '', `/?_at=${tok}&_lid=a`)
    const token = await import('../src/auth/token.js')
    const s60 = await import('../src/auth/strategy60.js')
    s60.bootstrap()
    return { token }
  }
  afterEach(() => { vi.unstubAllEnvs(); window.history.replaceState({}, '', '/') })

  it('default: still accepted (compatibility) and stripped from the URL', async () => {
    const { token } = await run(undefined)
    expect(token.getToken()).toBe(tok)
    expect(window.location.search).not.toContain('_at=')
  })

  it('VITE_ALLOW_URL_TOKEN=false: never used, still stripped from the URL', async () => {
    const { token } = await run('false')
    expect(token.getToken()).toBeNull()
    expect(window.location.search).not.toContain('_at=')
  })
})
