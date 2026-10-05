// M-26: preview/dev proxy tables.
import { describe, it, expect } from 'vitest'
import { BACKEND_ROUTES, devProxy, previewProxy } from '../proxy.js'

describe('proxy tables', () => {
  it('dev proxies every backend route prefix', () => {
    const p = devProxy('http://127.0.0.1:8003')
    expect(Object.keys(p)).toEqual(BACKEND_ROUTES)
    expect(BACKEND_ROUTES).toContain('/app-config')
    expect(p['/variance'].target).toBe('http://127.0.0.1:8003')
  })

  it('preview with an IIS-style API base strips that prefix', () => {
    const p = previewProxy('http://127.0.0.1:8003', '/DataVar6.0/api')
    const [pattern, rule] = Object.entries(p)[0]
    expect(Object.keys(p)).toHaveLength(1)
    expect(new RegExp(pattern).test('/DataVar6.0/api/variance/find')).toBe(true)
    expect(new RegExp(pattern).test('/DataVar6X0/api/variance/find')).toBe(false)   // the dot is literal
    expect(rule.rewrite('/DataVar6.0/api/variance/find?x=1')).toBe('/variance/find?x=1')
    expect(rule.target).toBe('http://127.0.0.1:8003')
  })

  it('a trailing slash on the base makes no difference', () => {
    const [, rule] = Object.entries(previewProxy('http://t', '/DataVar/api/'))[0]
    expect(rule.rewrite('/DataVar/api/auth/my-returns')).toBe('/auth/my-returns')
  })

  it('an empty or absolute base falls back to the dev table', () => {
    expect(Object.keys(previewProxy('http://t', ''))).toEqual(BACKEND_ROUTES)
    expect(Object.keys(previewProxy('http://t', 'https://api.example.com/x'))).toEqual(BACKEND_ROUTES)
  })
})
