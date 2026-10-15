/**
 * Shared PropTypes shapes for the Data Variance UI (code review M-44).
 *
 * The shapes describe what the API actually sends (docs/models.md) and are deliberately LOOSE:
 * every field is optional and unknown fields are allowed, because the backend adds fields over
 * time (truncated, duplicate_notice, resolution_mode, ...) and a strict shape would turn each
 * addition into a console warning. They exist to catch the mistakes that really happen -
 * a callback passed as a string, rows given as an object, a missing handler - and to document
 * each component's inputs. PropTypes are checked in development builds only; production
 * bundles strip the checks, so they cost nothing there.
 */
import PropTypes from 'prop-types'

const { string, number, bool, func, array, arrayOf, object, shape, oneOf, oneOfType, instanceOf } = PropTypes

export { PropTypes }

/** One option in a "which return / table?" question. */
export const optionShape = shape({ id: oneOfType([string, number]), label: string })

/** One candidate when a return name matched several returns. */
export const candidateShape = shape({
  return_id: oneOfType([string, number]),
  return_name: string,
  report_freq: string,
  score: number,
})

/** What /variance/find returns for a single return. */
export const returnInfoShape = shape({
  return_id: oneOfType([string, number]),
  return_name: string,
  report_freq: string,
  table_mapping_path: string,
  tables: arrayOf(shape({ table_name: string })),
})

/** A compute / nlresolve result. Loose on purpose (see the header comment). */
export const resultShape = shape({
  table_name: string,
  reporting_date: string,
  comparison_periods: arrayOf(string),
  columns: arrayOf(string),
  display_columns: arrayOf(string),
  rows: arrayOf(object),
  comparison_mode: string,
  chain_dates: arrayOf(string),
  missing_periods: arrayOf(string),
  truncated: bool,
  truncation_notice: string,
  duplicate_notice: string,
  removed_notice: string,
  error: string,
  error_code: string,
})

/** The "Understood as" block (/nlresolve interpretation). */
export const interpretationShape = shape({
  resolved_columns: arrayOf(string),
  resolved_column_labels: arrayOf(string),
  reporting_date: string,
  date_notes: arrayOf(string),
})

/** A pending clarification question. */
export const clarificationShape = shape({
  dimension: string,
  question: string,
  options: arrayOf(optionShape),
  skippable: bool,
  allowOther: bool,
  resolvedContext: object,
})

export const panelState = oneOf(['normal', 'expanded', 'minimized'])

export { string, number, bool, func, array, arrayOf, object, shape, oneOf, oneOfType, instanceOf }
