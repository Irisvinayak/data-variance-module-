// @vitest-environment jsdom
// M-34: App never stays on "Starting…" forever; retry works; no request loop.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'

const bootstrapAuth = vi.fn()
vi.mock('../src/auth/index.js', () => ({
  bootstrapAuth: (...a) => bootstrapAuth(...a),
  onIdentityChange: () => () => {},
}))
vi.mock('../src/components/LayoutContainer.jsx', () => ({
  default: ({ loginId }) => <div data-testid="layout">layout:{loginId}</div>,
}))

import App from '../src/App.jsx'

beforeEach(() => { bootstrapAuth.mockReset(); vi.spyOn(console, 'error').mockImplementation(() => {}) })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('M-34 App bootstrap', () => {
  it('shows Starting… then the application on success', async () => {
    let resolve
    bootstrapAuth.mockReturnValue(new Promise((r) => { resolve = r }))
    render(<App />)
    expect(screen.getByText('Starting…')).toBeTruthy()
    resolve({ loginId: 'iris810' })
    await waitFor(() => expect(screen.getByTestId('layout').textContent).toBe('layout:iris810'))
    expect(screen.queryByText('Starting…')).toBeNull()
  })

  it('a rejected bootstrap leaves "Starting…" for an error screen with Retry', async () => {
    bootstrapAuth.mockRejectedValue(new Error('boom'))
    render(<App />)
    await waitFor(() => expect(screen.getByText(/Could not start the application/)).toBeTruthy())
    expect(screen.queryByText('Starting…')).toBeNull()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
    expect(bootstrapAuth).toHaveBeenCalledTimes(1)     // no automatic loop
  })

  it('Retry succeeds -> application; each click is exactly one new attempt', async () => {
    bootstrapAuth.mockRejectedValueOnce(new Error('boom'))
    bootstrapAuth.mockResolvedValueOnce({ loginId: 'iris810' })
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByTestId('layout')).toBeTruthy())
    expect(bootstrapAuth).toHaveBeenCalledTimes(2)
  })

  it('failed retry shows the error screen again (still one attempt per click)', async () => {
    bootstrapAuth.mockRejectedValue(new Error('boom'))
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(bootstrapAuth).toHaveBeenCalledTimes(2))
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeTruthy()
    await new Promise((r) => setTimeout(r, 50))
    expect(bootstrapAuth).toHaveBeenCalledTimes(2)
  })

  it('an unmounted app ignores a late bootstrap result', async () => {
    let resolve
    bootstrapAuth.mockReturnValue(new Promise((r) => { resolve = r }))
    const { unmount } = render(<App />)
    unmount()
    resolve({ loginId: 'x' })
    await Promise.resolve()                              // would throw/warn if state were set
  })
})
