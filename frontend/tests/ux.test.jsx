// @vitest-environment jsdom
// M-25 date-selection stability and failure state, M-45 accessibility names/roles/keyboard,
// L-20 the chart panel is loaded lazily. api.js is faked; the real components are rendered.
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
vi.mock('react-resizable-panels', () => ({
  Panel: ({ children }) => <div>{children}</div>,
  Group: ({ children }) => <div>{children}</div>,
  Separator: ({ children }) => <div>{children}</div>,
}))
vi.mock('../src/components/TablePanel.jsx', () => ({ default: () => <div data-testid="table" /> }))
vi.mock('../src/components/VisualizationPanel.jsx', () => ({ default: () => <div data-testid="viz">chart</div> }))

import LayoutContainer from '../src/components/LayoutContainer.jsx'

const ret = (id) => ({
  return_id: String(id), return_name: `R${id}`, report_freq: 'Q',
  table_mapping_path: `p/${id}`, tables: [{ table_name: `T_${id}` }, { table_name: `T_${id}B` }],
})

beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset())
  api.getMyReturns.mockResolvedValue({ allowed_forms: ['1', '2'], allowed_count: 2 })
  api.getAvailableDates.mockResolvedValue({ dates: ['31-MAR-2025', '31-DEC-2024'] })
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

async function mountWithReturn() {
  api.findReturnTables.mockResolvedValue(ret(1))
  render(<LayoutContainer loginId="iris810" />)
  await waitFor(() => expect(api.getMyReturns).toHaveBeenCalled())
  await act(async () => {})
  fireEvent.change(screen.getByLabelText('Return name'), { target: { value: 'R1' } })
  fireEvent.click(screen.getByRole('button', { name: /^Search/ }))
  await waitFor(() => expect(api.getAvailableDates).toHaveBeenCalledTimes(1))
  await act(async () => {})
}
const openDates = () => fireEvent.click(screen.getByText(/Select date\(s\)|31-MAR-2025|31-DEC-2024/).closest('button'))

describe('M-25 date selection', () => {
  it('a search that returns the same return again does not refetch or wipe the ticked dates', async () => {
    await mountWithReturn()
    openDates()
    fireEvent.click(screen.getByLabelText('31-MAR-2025'))
    expect(document.querySelector('.ctrl-date-summary').textContent).toBe('31-MAR-2025')

    api.findReturnTables.mockResolvedValue(ret(1))                 // a NEW object with the same ids
    fireEvent.click(screen.getByRole('button', { name: /^Search/ }))
    await waitFor(() => expect(api.findReturnTables).toHaveBeenCalledTimes(2))
    await act(async () => {})
    expect(api.getAvailableDates).toHaveBeenCalledTimes(1)        // before the fix: 2, and the selection was cleared
    expect(document.querySelector('.ctrl-date-summary').textContent).toBe('31-MAR-2025')   // still ticked
  })

  it('a different table does refetch and starts with no dates ticked', async () => {
    await mountWithReturn()
    openDates()
    fireEvent.click(screen.getByLabelText('31-MAR-2025'))
    const select = document.querySelector('select')
    fireEvent.change(select, { target: { value: 'T_1B' } })
    await waitFor(() => expect(api.getAvailableDates).toHaveBeenCalledTimes(2))
    expect(api.getAvailableDates).toHaveBeenLastCalledWith('1', 'p/1', 'T_1B')
    await act(async () => {})
    expect(screen.getByText('Select date(s)…')).toBeTruthy()
  })

  it('a failed /dates request says so and offers a retry that works', async () => {
    api.getAvailableDates.mockRejectedValueOnce(new Error('boom'))
    api.findReturnTables.mockResolvedValue(ret(1))
    render(<LayoutContainer loginId="iris810" />)
    await waitFor(() => expect(api.getMyReturns).toHaveBeenCalled())
    await act(async () => {})
    fireEvent.change(screen.getByLabelText('Return name'), { target: { value: 'R1' } })
    fireEvent.click(screen.getByRole('button', { name: /^Search/ }))
    const retry = await screen.findByRole('alert')
    expect(retry.textContent).toMatch(/Could not load dates/)
    expect(document.body.textContent).not.toContain('No data found for this table')   // not mistaken for "no data"
    fireEvent.click(retry)
    await waitFor(() => expect(api.getAvailableDates).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getByText('Select date(s)…')).toBeTruthy())
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('M-45 accessibility', () => {
  it('the controls have accessible names', async () => {
    render(<LayoutContainer loginId="iris810" />)
    await waitFor(() => expect(api.getMyReturns).toHaveBeenCalled())
    await act(async () => {})
    expect(screen.getByLabelText('Ask a question about your data')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Ask the question' })).toBeTruthy()
    expect(screen.getByLabelText('Return name')).toBeTruthy()
  })

  it('the match picker is a labelled listbox of options, announces expansion and closes on Escape', async () => {
    api.findReturnTables.mockResolvedValue({ candidates: [
      { return_id: '1', return_name: 'R1', report_freq: 'Q', score: 0.9 },
      { return_id: '2', return_name: 'R2', report_freq: 'Q', score: 0.8 },
    ] })
    render(<LayoutContainer loginId="iris810" />)
    await waitFor(() => expect(api.getMyReturns).toHaveBeenCalled())
    await act(async () => {})
    fireEvent.change(screen.getByLabelText('Return name'), { target: { value: 'R' } })
    fireEvent.click(screen.getByRole('button', { name: /^Search/ }))
    const listbox = await screen.findByRole('listbox', { name: 'Matching returns' })
    expect(listbox).toBeTruthy()
    expect(screen.getAllByRole('option')).toHaveLength(2)
    const trigger = document.querySelector('.disambig-trigger')
    expect(trigger.getAttribute('aria-haspopup')).toBe('listbox')
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByLabelText('Filter matches')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy()

    fireEvent.keyDown(listbox, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull())
  })

  it('the date control announces that it opens a menu', async () => {
    await mountWithReturn()
    const trigger = screen.getByText('Select date(s)…').closest('button')
    expect(trigger.getAttribute('aria-haspopup')).toBe('true')
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(trigger)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
  })
})

describe('L-20 lazy chart panel', () => {
  it('the chart panel appears once a result exists (loaded on demand)', async () => {
    api.resolveNlQuery.mockResolvedValue({ table_name: 'T', rows: [{ id: 1 }], columns: [], display_columns: [] })
    render(<LayoutContainer loginId="iris810" />)
    await waitFor(() => expect(api.getMyReturns).toHaveBeenCalled())
    await act(async () => {})
    expect(screen.queryByTestId('viz')).toBeNull()                 // not loaded before it is needed
    fireEvent.change(screen.getByLabelText('Ask a question about your data'), { target: { value: 'loans' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask the question' }))
    expect(await screen.findByTestId('viz')).toBeTruthy()
  })

  it('the component imports the panel lazily, not statically', async () => {
    const fs = await import('node:fs')
    const path = await import('node:path')
    const src = fs.readFileSync(path.resolve(__dirname, '../src/components/LayoutContainer.jsx'), 'utf8')
    expect(src).toMatch(/lazy\(\(\) => import\('\.\/VisualizationPanel\.jsx'\)\)/)
    expect(src).not.toMatch(/^import VisualizationPanel/m)
  })
})
