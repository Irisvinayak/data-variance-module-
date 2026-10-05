// @vitest-environment jsdom
// H-17 stale responses, M-10 permissions fail closed, H-15 truncation notice,
// L-21 busy search button, L-27 no microphone. api.js is faked; the real
// LayoutContainer + ControlBar are rendered.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react'

const api = {
  findReturnTables: vi.fn(),
  computeVariance: vi.fn(),
  getMyReturns: vi.fn(),
  resolveNlQuery: vi.fn(),
  getAvailableDates: vi.fn(),
}
vi.mock('../src/api.js', () => ({
  findReturnTables: (...a) => api.findReturnTables(...a),
  computeVariance: (...a) => api.computeVariance(...a),
  getMyReturns: (...a) => api.getMyReturns(...a),
  resolveNlQuery: (...a) => api.resolveNlQuery(...a),
  getAvailableDates: (...a) => api.getAvailableDates(...a),
  SKIP_ANSWER: '__skip__',
}))
// Layout-only widgets: not under test, and they need browser APIs jsdom lacks.
vi.mock('react-resizable-panels', () => ({
  Panel: ({ children }) => <div>{children}</div>,
  Group: ({ children }) => <div>{children}</div>,
  Separator: ({ children }) => <div>{children}</div>,
}))
vi.mock('../src/components/TablePanel.jsx', () => ({
  default: ({ result }) => (
    <div data-testid="table">{result ? `rows:${result.rows?.length ?? 0}:${result.table_name}` : 'empty'}</div>
  ),
}))
vi.mock('../src/components/VisualizationPanel.jsx', () => ({ default: () => null }))

import LayoutContainer from '../src/components/LayoutContainer.jsx'

const deferred = () => {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
const ret = (id, name = `R${id}`) => ({
  return_id: String(id), return_name: name, report_freq: 'Q',
  table_mapping_path: `p/${id}`, tables: [{ table_name: `T_${id}` }],
})
const nlResult = (extra = {}) => ({
  table_name: 'T_NL', rows: [{ id: 1 }], columns: [], display_columns: [], ...extra,
})

beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset())
  api.getMyReturns.mockResolvedValue({ allowed_forms: ['1', '2', '3'], allowed_count: 3 })
  api.getAvailableDates.mockResolvedValue({ dates: ['31-MAR-2025', '31-DEC-2024'] })
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

async function mount() {
  render(<LayoutContainer loginId="iris810" />)
  await waitFor(() => expect(api.getMyReturns).toHaveBeenCalled())
  await act(async () => {})
}
const typeReturn = (v) => fireEvent.change(screen.getByPlaceholderText(/CIMS/), { target: { value: v } })
const searchBtn = () => screen.getByRole('button', { name: /^Search|Searching/ })
const nlInput = () => screen.getByPlaceholderText('Ask...')
const nlButton = () => screen.getByRole('button', { name: 'Ask the question' })
const askNl = (q) => { fireEvent.change(nlInput(), { target: { value: q } }); fireEvent.click(nlButton()) }
// Typing a new question in the NL box clears the page and drops in-flight responses.
const abandon = () => fireEvent.change(nlInput(), { target: { value: 'something else' } })
const shown = (text) => document.body.textContent.includes(text)

describe('H-17 stale responses', () => {
  it('find: late response A (after reset + new search B) cannot overwrite B', async () => {
    const A = deferred(); const B = deferred()
    api.findReturnTables.mockImplementationOnce(() => A.promise).mockImplementationOnce(() => B.promise)
    await mount()

    typeReturn('A'); fireEvent.click(searchBtn())
    await waitFor(() => expect(api.findReturnTables).toHaveBeenCalledTimes(1))
    abandon()                                                // abandon A
    typeReturn('B'); fireEvent.click(searchBtn())
    await waitFor(() => expect(api.findReturnTables).toHaveBeenCalledTimes(2))

    await act(async () => { B.resolve(ret(2)) })             // B finishes first
    await waitFor(() => expect(shown('T_2')).toBe(true))
    await act(async () => { A.resolve(ret(1)) })             // A arrives late
    expect(shown('T_2')).toBe(true)
    expect(shown('T_1')).toBe(false)
  })

  it('find: a late FAILURE of A does not put an error over B', async () => {
    const A = deferred(); const B = deferred()
    api.findReturnTables.mockImplementationOnce(() => A.promise).mockImplementationOnce(() => B.promise)
    await mount()
    typeReturn('A'); fireEvent.click(searchBtn())
    await waitFor(() => expect(api.findReturnTables).toHaveBeenCalledTimes(1))
    abandon()
    typeReturn('B'); fireEvent.click(searchBtn())
    await act(async () => { B.resolve(ret(2)) })
    await act(async () => { A.reject(new Error('A exploded')) })
    expect(shown('A exploded')).toBe(false)
    expect(shown('T_2')).toBe(true)
  })

  it('candidate selection: late response for candidate 1 cannot overwrite candidate 2', async () => {
    const first = deferred()
    api.findReturnTables.mockImplementation((q) => {
      if (q === 'CIM') return Promise.resolve({ candidates: [
        { return_id: '1', return_name: 'R1', report_freq: 'Q', score: 0.9 },
        { return_id: '2', return_name: 'R2', report_freq: 'Q', score: 0.8 }] })
      if (q === 'R1') return first.promise
      if (q === 'R2') return Promise.resolve(ret(2))
      return Promise.reject(new Error('unexpected ' + q))
    })
    await mount()
    typeReturn('CIM'); fireEvent.click(searchBtn())
    fireEvent.click(await screen.findByText('R1'))          // pending
    await waitFor(() => expect(api.findReturnTables).toHaveBeenCalledWith('R1'))
    abandon()                                                // user starts over
    typeReturn('CIM'); fireEvent.click(searchBtn())
    fireEvent.click(await screen.findByText('R2'))
    await waitFor(() => expect(shown('T_2')).toBe(true))
    await act(async () => { first.resolve(ret(1)) })         // stale
    expect(shown('T_2')).toBe(true)
    expect(shown('T_1')).toBe(false)
  })
})

describe('M-10 permissions fail closed', () => {
  async function tryFind(allowedResponse) {
    api.getMyReturns.mockImplementation(allowedResponse)
    api.findReturnTables.mockResolvedValue(ret(9))
    await mount()
    typeReturn('R9'); fireEvent.click(searchBtn())
    await act(async () => {})
  }

  it('allowed return passes', async () => {
    await tryFind(() => Promise.resolve({ allowed_forms: ['9'] }))
    await waitFor(() => expect(shown('T_9')).toBe(true))
  })

  it('a return that is not in the allowed list is refused', async () => {
    await tryFind(() => Promise.resolve({ allowed_forms: ['1'] }))
    await waitFor(() => expect(shown('do not have access')).toBe(true))
    expect(shown('T_9')).toBe(false)
  })

  it('failed API request -> denied, with an explicit message', async () => {
    await tryFind(() => Promise.reject(new Error('500')))
    await waitFor(() => expect(shown('Permissions are unavailable')).toBe(true))
    expect(shown('T_9')).toBe(false)
    expect(api.findReturnTables).not.toHaveBeenCalled()
  })

  it('timeout (the api layer rejects with the timeout error) -> denied', async () => {
    await tryFind(() => Promise.reject(new Error('The request timed out. Please try again.')))
    await waitFor(() => expect(shown('Permissions are unavailable')).toBe(true))
    expect(shown('T_9')).toBe(false)
  })

  it('malformed response (no allowed_forms) -> not "allowed"... see note on empty set', async () => {
    await tryFind(() => Promise.resolve({}))
    // Documented behaviour: a SUCCESSFUL empty list means "backend auth is off"
    // (allowed_count 0) and the UI does not filter; the server still enforces.
    await waitFor(() => expect(shown('T_9')).toBe(true))
  })

  it('NL search is also refused when permissions failed', async () => {
    api.getMyReturns.mockRejectedValue(new Error('500'))
    await mount()
    askNl('total loans')
    await act(async () => {})
    expect(api.resolveNlQuery).not.toHaveBeenCalled()
  })

  it('no loginId -> nothing is allowed through to a request', async () => {
    render(<LayoutContainer loginId="" />)
    await act(async () => {})
    expect(api.getMyReturns).not.toHaveBeenCalled()
  })
})

describe('H-15 truncation reaches the user', () => {
  it('truncated result shows the backend notice', async () => {
    api.resolveNlQuery.mockResolvedValue(nlResult({ truncated: true, truncation_notice: 'Only the first 50000 rows were read.' }))
    await mount()
    askNl('loans')
    await waitFor(() => expect(shown('Only the first 50000 rows were read.')).toBe(true))
  })

  it('truncated without a notice text still warns', async () => {
    api.resolveNlQuery.mockResolvedValue(nlResult({ truncated: true }))
    await mount()
    askNl('loans')
    await waitFor(() => expect(shown('cut off at the maximum number of rows')).toBe(true))
  })

  it('complete results show no truncation warning', async () => {
    api.resolveNlQuery.mockResolvedValue(nlResult({ truncated: false }))
    await mount()
    askNl('loans')
    await waitFor(() => expect(screen.getByTestId('table').textContent).toContain('T_NL'))
    expect(shown('cut off')).toBe(false)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('the warning does not vanish on its own while the truncated result is on screen', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    api.resolveNlQuery.mockResolvedValue(nlResult({ truncated: true, truncation_notice: 'CUT-OFF-NOTICE' }))
    await mount()
    askNl('loans')
    await waitFor(() => expect(shown('CUT-OFF-NOTICE')).toBe(true))
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expect(shown('CUT-OFF-NOTICE')).toBe(true)
    vi.useRealTimers()
  })

  it('the warning is gone once the page is cleared (the truncated result is gone)', async () => {
    api.resolveNlQuery.mockResolvedValue(nlResult({ truncated: true, truncation_notice: 'CUT-OFF-NOTICE' }))
    await mount()
    askNl('loans')
    await waitFor(() => expect(shown('CUT-OFF-NOTICE')).toBe(true))
    fireEvent.change(nlInput(), { target: { value: 'a different question' } })
    expect(shown('CUT-OFF-NOTICE')).toBe(false)
  })
})

describe('L-21 search button while a request runs', () => {
  it('return search: enabled, then disabled while loading, then enabled after success', async () => {
    const A = deferred()
    api.findReturnTables.mockReturnValueOnce(A.promise)
    await mount()
    typeReturn('A')
    expect(searchBtn().disabled).toBe(false)
    fireEvent.click(searchBtn())
    await waitFor(() => expect(searchBtn().disabled).toBe(true))
    fireEvent.click(searchBtn()); fireEvent.click(searchBtn())          // extra clicks
    fireEvent.keyDown(screen.getByPlaceholderText(/CIMS/), { key: 'Enter' })
    expect(api.findReturnTables).toHaveBeenCalledTimes(1)
    await act(async () => { A.resolve(ret(1)) })
    await waitFor(() => expect(shown('T_1')).toBe(true))
    expect(screen.queryByText(/Searching/)).toBeNull()
  })

  it('return search: enabled again after failure and after a timeout error', async () => {
    api.findReturnTables.mockRejectedValueOnce(new Error('boom'))
      .mockRejectedValueOnce(new Error('The request timed out. Please try again.'))
    await mount()
    typeReturn('A'); fireEvent.click(searchBtn())
    await waitFor(() => expect(shown('boom')).toBe(true))
    expect(searchBtn().disabled).toBe(false)
    fireEvent.click(searchBtn())
    await waitFor(() => expect(shown('timed out')).toBe(true))
    expect(searchBtn().disabled).toBe(false)
  })

  it('NL search: the button is disabled while loading and restored afterwards', async () => {
    const A = deferred()
    api.resolveNlQuery.mockReturnValueOnce(A.promise)
    await mount()
    askNl('loans')
    await waitFor(() => expect(nlButton().disabled).toBe(true))
    fireEvent.click(nlButton()); fireEvent.click(nlButton())
    expect(api.resolveNlQuery).toHaveBeenCalledTimes(1)
    await act(async () => { A.reject(new Error('The request timed out. Please try again.')) })
    await waitFor(() => expect(nlButton().disabled).toBe(false))
  })

  it('NL search: pressing Enter while a request is running does not send a duplicate', async () => {
    const A = deferred()
    api.resolveNlQuery.mockReturnValueOnce(A.promise)
    await mount()
    fireEvent.change(nlInput(), { target: { value: 'loans' } })
    fireEvent.keyDown(nlInput(), { key: 'Enter' })
    await waitFor(() => expect(nlButton().disabled).toBe(true))
    fireEvent.keyDown(nlInput(), { key: 'Enter' })
    fireEvent.keyDown(nlInput(), { key: 'Enter' })
    expect(api.resolveNlQuery).toHaveBeenCalledTimes(1)
    await act(async () => { A.resolve(nlResult()) })
  })
})

describe('L-27 microphone button', () => {
  it('is not rendered, and the other toolbar controls are', async () => {
    await mount()
    expect(document.body.textContent).not.toContain('🎤')
    expect(screen.queryByTitle('Voice Input')).toBeNull()
    expect(nlInput()).toBeTruthy()
    expect(nlButton()).toBeTruthy()
    expect(screen.getByPlaceholderText(/CIMS/)).toBeTruthy()
    expect(searchBtn()).toBeTruthy()
  })
})

describe('H-13 / M-15 data-quality notices', () => {
  it('shows the duplicate-key and removed-row notes while the result is on screen', async () => {
    api.resolveNlQuery.mockResolvedValue(nlResult({
      duplicate_notice: '2 row key(s) appear more than once in the same period',
      removed_notice: '3 row(s) present in a comparison period are missing from the current period.',
    }))
    await mount()
    askNl('loans')
    await waitFor(() => expect(shown('appear more than once')).toBe(true))
    expect(shown('3 row(s) present')).toBe(true)
    expect(screen.queryByRole('alert')).toBeNull()          // not the truncation alert
  })

  it('shows nothing when the backend sent no notes', async () => {
    api.resolveNlQuery.mockResolvedValue(nlResult())
    await mount()
    askNl('loans')
    await waitFor(() => expect(screen.getByTestId('table').textContent).toContain('T_NL'))
    expect(shown('more than once')).toBe(false)
    expect(shown('row(s) present')).toBe(false)
  })
})
