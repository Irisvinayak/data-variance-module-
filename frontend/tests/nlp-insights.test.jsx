// @vitest-environment jsdom
// The NL answer panel (NlpInsights), the short-choice questions, and the follow-up
// round-trip: conversation_state from one answer is sent with the next question.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'

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
vi.mock('../src/components/VisualizationPanel.jsx', () => ({ default: () => <div data-testid="viz" /> }))

import LayoutContainer from '../src/components/LayoutContainer.jsx'
import { resultToCsv } from '../src/components/NlpInsights.jsx'

const RESULT = {
  columns: ['AMT'],
  rows: [{
    identifier: 'E', display_label: 'Element E',
    current: { AMT: 0 }, previous: { previous_1: { AMT: { value: 500 } } },
    analysis: { cells: { AMT: { change: '-500.00', pct_change: '-100.00', status: 'became_zero' } } },
  }],
  summary: '1 of 6 rows match (decreases only).',
  applied_filters: ['decreases only'],
  analysis_notes: ["'Significant' was read as a change of more than 10% in either direction."],
  provenance: { return_id: 'R1', table: 'T', current_date: '31-MAR-2025', previous_dates: ['31-DEC-2024'], formula: 'F' },
  interpretation: { comparison_dates: ['31-MAR-2025', '31-DEC-2024'] },
  conversation_state: { turns: ['Only decreases'], table: 'T', return_id: 'R1', identifiers: ['E'] },
}

beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset())
  api.getMyReturns.mockResolvedValue({ allowed_forms: ['1'], allowed_count: 1 })
  api.getAvailableDates.mockResolvedValue({ dates: [] })
})
afterEach(cleanup)

function ask(text) {
  const box = screen.getByLabelText('Ask a question about your data')
  fireEvent.change(box, { target: { value: text } })
  fireEvent.keyDown(box, { key: 'Enter' })
}

describe('NL answer panel', () => {
  it('shows the summary, filters and notes the backend sent', async () => {
    api.resolveNlQuery.mockResolvedValue(RESULT)
    render(<LayoutContainer />)
    ask('Only decreases')
    expect(await screen.findByText(RESULT.summary)).toBeTruthy()
    expect(screen.getByText('decreases only')).toBeTruthy()
    expect(screen.getByText(RESULT.analysis_notes[0])).toBeTruthy()
  })

  it('sends the previous conversation_state with the next question', async () => {
    api.resolveNlQuery.mockResolvedValue(RESULT)
    render(<LayoutContainer />)
    ask('Compare current and previous')
    await screen.findByText(RESULT.summary)
    ask('More than 20%')
    await waitFor(() => expect(api.resolveNlQuery).toHaveBeenCalledTimes(2))
    expect(api.resolveNlQuery.mock.calls[1][1].conversationState).toEqual(RESULT.conversation_state)
  })

  it('"New question" stops the next query being a follow-up', async () => {
    api.resolveNlQuery.mockResolvedValue(RESULT)
    render(<LayoutContainer />)
    ask('Compare current and previous')
    fireEvent.click(await screen.findByText('New question'))
    ask('Show top 5')
    await waitFor(() => expect(api.resolveNlQuery).toHaveBeenCalledTimes(2))
    expect(api.resolveNlQuery.mock.calls[1][1].conversationState).toBeNull()
  })

  it('answers a measure question with the picked option', async () => {
    api.resolveNlQuery.mockResolvedValueOnce({
      needs_clarification: true, dimension: 'measure', question: 'Percentage or absolute?',
      options: [{ id: 'pct', label: 'Percentage change' }, { id: 'abs', label: 'Absolute change (amount)' }],
      skippable: true, resolved_context: { query: 'Show highest variation' },
    }).mockResolvedValueOnce(RESULT)
    render(<LayoutContainer />)
    ask('Show highest variation')
    fireEvent.click(await screen.findByText('Absolute change (amount)'))
    await waitFor(() => expect(api.resolveNlQuery).toHaveBeenCalledTimes(2))
    const opts = api.resolveNlQuery.mock.calls[1][1]
    expect(opts.dimension).toBe('measure')
    expect(opts.clarificationAnswer).toBe('abs')
  })

  it('a typed threshold is sent as the answer, not folded into the query', async () => {
    api.resolveNlQuery.mockResolvedValueOnce({
      needs_clarification: true, dimension: 'threshold', question: 'What threshold?',
      options: [{ id: '10', label: 'More than 10%' }], skippable: true, allow_other: true,
      resolved_context: { query: 'Show significant changes' },
    }).mockResolvedValueOnce(RESULT)
    render(<LayoutContainer />)
    ask('Show significant changes')
    const box = await screen.findByLabelText('Type a threshold')
    fireEvent.change(box, { target: { value: '5 lakh' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() => expect(api.resolveNlQuery).toHaveBeenCalledTimes(2))
    const [query, opts] = api.resolveNlQuery.mock.calls[1]
    expect(query).toBe('Show significant changes')
    expect(opts.dimension).toBe('threshold')
    expect(opts.clarificationAnswer).toBe('5 lakh')
  })
})

describe('CSV export', () => {
  it('writes one line per element and column with the backend figures', () => {
    const csv = resultToCsv(RESULT).split('\n')
    expect(csv[0]).toBe(`# ${RESULT.summary}`)
    expect(csv).toContain('Element E,E,AMT,0,500,-500.00,-100.00,became_zero')
  })
})
