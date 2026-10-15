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

// ── M-45: the complete keyboard pattern ──────────────────────────────────────

const CANDS = [
  { return_id: '1', return_name: 'Alpha', report_freq: 'Q', score: 0.9 },
  { return_id: '2', return_name: 'Beta', report_freq: 'Q', score: 0.8 },
  { return_id: '3', return_name: 'Gamma', report_freq: 'Q', score: 0.7 },
]

async function openPicker() {
  api.findReturnTables.mockImplementation((q) => {
    if (q === 'R') return Promise.resolve({ candidates: CANDS })
    const hit = CANDS.find((c) => c.return_name === q || String(c.return_id) === q)
    return hit ? Promise.resolve(ret(hit.return_id)) : Promise.reject(new Error('unexpected ' + q))
  })
  api.getMyReturns.mockResolvedValue({ allowed_forms: ['1', '2', '3'], allowed_count: 3 })
  render(<LayoutContainer loginId="iris810" />)
  await waitFor(() => expect(api.getMyReturns).toHaveBeenCalled())
  await act(async () => {})
  fireEvent.change(screen.getByLabelText('Return name'), { target: { value: 'R' } })
  fireEvent.click(screen.getByRole('button', { name: /^Search/ }))
  await screen.findByRole('listbox', { name: 'Matching returns' })
  return screen.getByRole('combobox', { name: 'Filter matches' })
}

const activeName = (input) => {
  const id = input.getAttribute('aria-activedescendant')
  return id ? document.getElementById(id).textContent : null
}

describe('M-45 keyboard navigation', () => {
  it('the filter is a combobox that controls the listbox and starts on the first option', async () => {
    const input = await openPicker()
    const listbox = screen.getByRole('listbox')
    expect(input.getAttribute('aria-controls')).toBe(listbox.id)
    expect(input.getAttribute('aria-autocomplete')).toBe('list')
    expect(input.getAttribute('aria-expanded')).toBe('true')
    expect(activeName(input)).toContain('Alpha')
    const options = screen.getAllByRole('option')
    expect(options.map((o) => o.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false'])
  })

  it('arrows move the highlight, wrap around, and focus stays in the input', async () => {
    const input = await openPicker()
    input.focus()
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(activeName(input)).toContain('Beta')
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(activeName(input)).toContain('Gamma')
    fireEvent.keyDown(input, { key: 'ArrowDown' })                  // wraps to the top
    expect(activeName(input)).toContain('Alpha')
    fireEvent.keyDown(input, { key: 'ArrowUp' })                    // wraps to the bottom
    expect(activeName(input)).toContain('Gamma')
    expect(document.activeElement).toBe(input)
  })

  it('Home and End jump to the ends', async () => {
    const input = await openPicker()
    fireEvent.keyDown(input, { key: 'End' })
    expect(activeName(input)).toContain('Gamma')
    fireEvent.keyDown(input, { key: 'Home' })
    expect(activeName(input)).toContain('Alpha')
  })

  it('Enter chooses the highlighted option', async () => {
    const input = await openPicker()
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(document.body.textContent).toContain('T_2'))
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('typing filters the list, resets the highlight, and Enter picks the match', async () => {
    const input = await openPicker()
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.change(input, { target: { value: 'gam' } })
    expect(screen.getAllByRole('option')).toHaveLength(1)
    expect(activeName(input)).toContain('Gamma')
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(document.body.textContent).toContain('T_3'))
  })

  it('with no matches the arrows and Enter do nothing and nothing is announced', async () => {
    const input = await openPicker()
    fireEvent.change(input, { target: { value: 'zzz' } })
    expect(screen.queryAllByRole('option')).toHaveLength(0)
    expect(input.getAttribute('aria-activedescendant')).toBeNull()
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(api.findReturnTables).toHaveBeenCalledTimes(1)           // only the first search
  })

  it('Escape closes the picker', async () => {
    const input = await openPicker()
    fireEvent.keyDown(input, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull())
  })

  it('the mouse still works: clicking an option chooses it', async () => {
    await openPicker()
    fireEvent.click(screen.getByRole('option', { name: /Beta/ }))
    await waitFor(() => expect(document.body.textContent).toContain('T_2'))
  })

  it('hovering an option highlights it', async () => {
    const input = await openPicker()
    fireEvent.mouseEnter(screen.getByRole('option', { name: /Gamma/ }))
    expect(activeName(input)).toContain('Gamma')
  })

  it('other keys are left alone (typing is not swallowed)', async () => {
    const input = await openPicker()
    const notPrevented = fireEvent.keyDown(input, { key: 'a' })
    expect(notPrevented).toBe(true)
  })

  it('the clarification picker for a natural-language question has the same keyboard support', async () => {
    api.resolveNlQuery.mockResolvedValue({
      needs_clarification: true, dimension: 'return', question: 'Which return?',
      options: [{ id: 'a', label: 'Return A' }, { id: 'b', label: 'Return B' }], skippable: true,
    })
    render(<LayoutContainer loginId="iris810" />)
    await waitFor(() => expect(api.getMyReturns).toHaveBeenCalled())
    await act(async () => {})
    fireEvent.change(screen.getByLabelText('Ask a question about your data'), { target: { value: 'loans' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask the question' }))
    const input = await screen.findByRole('combobox', { name: 'Filter returns' })
    expect(activeName(input)).toContain('Return A')
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(activeName(input)).toContain('Return B')
    api.resolveNlQuery.mockResolvedValue({ table_name: 'T_NL', rows: [], columns: [], display_columns: [] })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(api.resolveNlQuery).toHaveBeenCalledTimes(2))
    expect(api.resolveNlQuery.mock.calls[1][1].clarificationAnswer).toBe('b')
  })
})
