/**
 * Shared constants and helpers for the Data Variance feature.
 */

export const VARIANCE_STEPS = {
  RETURN_NAME:   'return_name',
  DISAMBIGUATE:  'disambiguate',   // multiple candidates — user must pick
  TABLE:         'table',
  DATE:          'date',
  PERIODS:       'periods',
  RESULT:        'result',
}

export function freqLabel(freq) {
  const f = (freq || '').toUpperCase()
  const labels = {
    A: 'Annually (Financial Year, 31-Mar)',   ANNUAL: 'Annually (Financial Year, 31-Mar)',
    Y: 'Annually (Financial Year, 31-Mar)',   FY:     'Annually (Financial Year, 31-Mar)',
    B: 'Annually (Calendar Year, 31-Dec)',    CY:     'Annually (Calendar Year, 31-Dec)',
    Q: 'Quarterly (31-Mar / 30-Jun / 30-Sep / 31-Dec)',
    QUARTERLY: 'Quarterly (31-Mar / 30-Jun / 30-Sep / 31-Dec)',
    H: 'Half-Yearly Financial (31-Mar / 30-Sep)',
    HALFYEARLY: 'Half-Yearly Financial (31-Mar / 30-Sep)',
    HY: 'Half-Yearly Financial (31-Mar / 30-Sep)',
    FH: 'Half-Yearly Financial (31-Mar / 30-Sep)',
    C: 'Half-Yearly Calendar (30-Jun / 31-Dec)',
    CH: 'Half-Yearly Calendar (30-Jun / 31-Dec)',
    M: 'Monthly (last day of month)',   MONTHLY:     'Monthly (last day of month)',
    W: 'Weekly (Fridays)',              WEEKLY:      'Weekly (Fridays)',
    F: 'Fortnightly (15th or last day)', FORTNIGHTLY: 'Fortnightly (15th or last day)',
    HM: 'Half-Monthly (15th or last day)',
    D: 'Daily',  DAILY: 'Daily',
  }
  return labels[f] || freq || '—'
}

export function dateHintForFreq(freq) {
  const f = (freq || '').toUpperCase()
  const y = new Date().getFullYear()
  if (['A', 'ANNUAL', 'Y', 'FY'].includes(f))
    return { example: `31-MAR-${y}`, hint: 'Financial year end — must be 31-Mar.' }
  if (['B', 'CY'].includes(f))
    return { example: `31-DEC-${y}`, hint: 'Calendar year end — must be 31-Dec.' }
  if (['Q', 'QUARTERLY'].includes(f))
    return { example: `31-MAR-${y}`, hint: 'Quarter end — 31-Mar / 30-Jun / 30-Sep / 31-Dec.' }
  if (['H', 'HALFYEARLY', 'HY', 'FH'].includes(f))
    return { example: `31-MAR-${y}`, hint: 'Financial half-year — 31-Mar or 30-Sep.' }
  if (['C', 'CH'].includes(f))
    return { example: `30-JUN-${y}`, hint: 'Calendar half-year — 30-Jun or 31-Dec.' }
  if (['W', 'WEEKLY'].includes(f))
    return { example: 'any Friday', hint: 'Weekly — must be a Friday.' }
  if (['F', 'FORTNIGHTLY', 'HM'].includes(f))
    return { example: `15-MAR-${y}`, hint: 'Fortnightly — 15th or last day of month.' }
  if (['D', 'DAILY', 'G'].includes(f))
    return { example: `26-MAY-${y}`, hint: 'Daily — any valid past date.' }
  return { example: `31-MAR-${y}`, hint: 'Monthly — last day of the month.' }
}
