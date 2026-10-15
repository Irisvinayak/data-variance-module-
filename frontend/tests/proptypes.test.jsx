// @vitest-environment jsdom
// M-44: the declared PropTypes match what the application really passes. Real LayoutContainer,
// ControlBar, TablePanel, PanelHeader and ColumnVisibilityMenu are rendered through the main flows
// with a spy on console.error; any "Failed prop type" fails the test. (The chart panel is replaced:
// it needs browser layout APIs jsdom lacks.) Also proves the checks are live by breaking one.
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
vi.mock('../src/components/VisualizationPanel.jsx', () => ({ default: () => <div data-testid="viz" /> }))

import LayoutContainer from '../src/components/LayoutContainer.jsx'
import NoticeToast from '../src/components/NoticeToast.jsx'
import ColumnVisibilityMenu from '../src/components/ColumnVisibilityMenu.jsx'
import PanelHeader from '../src/components/PanelHeader.jsx'

const RESULT = {
  table_name: 'T', reporting_date: '31-MAR-2025', comparison_periods: ['31-DEC-2024'],
  columns: ['AMT'], display_columns: ['AMT'], comparison_mode: 'vs_current', chain_dates: [], missing_periods: [],
  rows: [{
    identifier: 'A', display_label: 'A', current: { AMT: 110 },
    previous: { previous_1: { AMT: {
      value: 100, change: { value: '10.00', color: 'success' }, pct_change: { value: '10.00%', color: 'success' },
      variance_summary: { text: '110.00 up 10.00% (Prev: 100.00)', arrow: 'up', color: 'success' },
    } } },
  }],
}
const ret = (id) => ({
  return_id: String(id), return_name: `R${id}`, report_freq: 'Q', table_mapping_path: `p/${id}`,
  tables: [{ table_name: `T_${id}` }, { table_name: `T_${id}B` }],
})

let errors
beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset())
  api.getMyReturns.mockResolvedValue({ allowed_forms: ['1', '2'], allowed_count: 2 })
  api.getAvailableDates.mockResolvedValue({ dates: ['31-MAR-2025', '31-DEC-2024'] })
  errors = vi.spyOn(console, 'error').mockImplementation(() => {})
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

// React logs these as console.error('Warning: Failed %s type: %s%s', 'prop', message, stack).
const propTypeWarnings = () => errors.mock.calls.map((c) => c.map(String).join(' ')).filter((m) => /Failed .*type/.test(m))

async function mount() {
  render(<LayoutContainer loginId="iris810" />)
  await waitFor(() => expect(api.getMyReturns).toHaveBeenCalled())
  await act(async () => {})
}

describe('the declared PropTypes match real usage', () => {
  it('idle toolbar', async () => {
    await mount()
    expect(propTypeWarnings()).toEqual([])
  })

  it('a natural-language result with the table panel, header and column menu', async () => {
    api.resolveNlQuery.mockResolvedValue({
      ...RESULT, return_id: '1', return_name: 'R1', confidence: 0.9, truncated: true, truncation_notice: 'cut',
      duplicate_notice: 'dup', removed_notice: 'gone',
      interpretation: { resolved_columns: ['AMT'], resolved_column_labels: ['Amount'], reporting_date: '31-MAR-2025', date_notes: [] },
    })
    await mount()
    fireEvent.change(screen.getByLabelText('Ask a question about your data'), { target: { value: 'loans' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask the question' }))
    await screen.findByTestId('viz')
    expect(propTypeWarnings()).toEqual([])
  })

  it('a return search that finds several candidates, then a selection', async () => {
    api.findReturnTables.mockImplementation((q) => (q === 'R'
      ? Promise.resolve({ candidates: [{ return_id: '1', return_name: 'R1', report_freq: 'Q', score: 0.9 },
        { return_id: '2', return_name: 'R2', report_freq: 'Q', score: 0.8 }] })
      : Promise.resolve(ret(q.replace('R', '')))))
    await mount()
    fireEvent.change(screen.getByLabelText('Return name'), { target: { value: 'R' } })
    fireEvent.click(screen.getByRole('button', { name: /^Search/ }))
    fireEvent.click(await screen.findByRole('option', { name: /R2/ }))
    await waitFor(() => expect(document.body.textContent).toContain('T_2'))
    await waitFor(() => expect(api.getAvailableDates).toHaveBeenCalled())
    await act(async () => {})
    expect(propTypeWarnings()).toEqual([])
  })

  it('a clarification question with options', async () => {
    api.resolveNlQuery.mockResolvedValue({
      needs_clarification: true, dimension: 'return', question: 'Which return?',
      options: [{ id: 'a', label: 'Return A' }, { id: 'b', label: 'Return B' }],
      skippable: true, allow_other: true, confidence: 0, resolved_context: { query: 'q' },
      interpretation: { resolved_columns: [], date_notes: [] },
    })
    await mount()
    fireEvent.change(screen.getByLabelText('Ask a question about your data'), { target: { value: 'loans' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask the question' }))
    await screen.findByRole('combobox', { name: 'Filter returns' })
    expect(propTypeWarnings()).toEqual([])
  })

  it('an error result and a failed date request', async () => {
    api.getAvailableDates.mockRejectedValue(new Error('x'))
    api.findReturnTables.mockResolvedValue(ret(1))
    await mount()
    fireEvent.change(screen.getByLabelText('Return name'), { target: { value: 'R1' } })
    fireEvent.click(screen.getByRole('button', { name: /^Search/ }))
    await screen.findByRole('alert')
    expect(propTypeWarnings()).toEqual([])
  })

  it('the leaf components with their real props', () => {
    render(
      <>
        <NoticeToast message="hello" onDismiss={() => {}} />
        <ColumnVisibilityMenu columns={['A', 'B']} comparableSet={new Set(['A'])} hidden={[]} onToggle={() => {}} />
        <PanelHeader title="t" icon="x" onExpand={() => {}} onMinimize={() => {}} isExpanded={false} isMinimized={false} />
      </>,
    )
    expect(propTypeWarnings()).toEqual([])
  })
})

describe('the checks are live', () => {
  it('a missing required callback is reported', () => {
    render(<NoticeToast message="x" />)
    expect(propTypeWarnings().join('\n')).toMatch(/onDismiss.*NoticeToast|NoticeToast.*onDismiss/s)
  })

  it('a wrongly typed prop is reported', () => {
    render(<ColumnVisibilityMenu columns={[]} onToggle="not-a-function" />)
    expect(propTypeWarnings().join('\n')).toMatch(/onToggle/)
  })
})
