/**
 * DatePicker.jsx — standalone calendar component.
 * Replaces <input type="date"> in InputPanel.
 *
 * Props:
 *   value      string  current date in 'DD-MMM-YYYY'
 *   onChange   fn      called with 'DD-MMM-YYYY' string
 *   disabled   bool    greys out the whole picker
 *
 * Fixes:
 *   1. buildGrid: correctly handles prev-month overflow when month === 0
 *      (was calling daysInMonth(year, -1) without adjusting year)
 *   2. commitYearInput: now calls setYearInput(String(y)) on valid entry
 *      so re-opening year edit shows the committed value, not the old one
 *   3. useEffect dependency: recomputes parsed inside the effect instead
 *      of closing over the outer `parsed` which could be stale
 *   4. Dropdown positioning: adds dp-dropdown--right class when trigger is
 *      in the right half of the viewport to prevent off-screen clipping
 */

import { useState, useEffect, useRef, useCallback } from 'react'

const MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
const MONTH_NAMES = ['January','February','March','April','May','June',
                     'July','August','September','October','November','December']
const DOW = ['Su','Mo','Tu','We','Th','Fr','Sa']

// ---------- helpers --------------------------------------------------------

function parseDDMMMYYYY(str) {
  if (!str) return null
  const m = str.match(/^(\d{2})-([A-Za-z]{3})-(\d{4})$/)
  if (!m) return null
  const mon = MONTHS.indexOf(m[2].toUpperCase())
  if (mon === -1) return null
  return new Date(+m[3], mon, +m[1])
}

function toDDMMMYYYY(date) {
  if (!date) return ''
  const d = String(date.getDate()).padStart(2, '0')
  const m = MONTHS[date.getMonth()]
  return `${d}-${m}-${date.getFullYear()}`
}

function sameDay(a, b) {
  return a && b && a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate()
}

function daysInMonth(year, month) {
  return new Date(year, month + 1, 0).getDate()
}

function buildGrid(year, month) {
  const first = new Date(year, month, 1).getDay() // 0 = Sun
  const total = daysInMonth(year, month)
  const cells = []

  // FIX #1: correctly handle January (month === 0) by stepping back the year
  const prevMonthYear  = month === 0 ? year - 1 : year
  const prevMonthIndex = month === 0 ? 11 : month - 1
  const prevTotal = daysInMonth(prevMonthYear, prevMonthIndex)

  for (let i = first - 1; i >= 0; i--) {
    cells.push({ date: new Date(prevMonthYear, prevMonthIndex, prevTotal - i), outOfMonth: true })
  }
  for (let d = 1; d <= total; d++) {
    cells.push({ date: new Date(year, month, d), outOfMonth: false })
  }
  const remaining = 42 - cells.length
  for (let d = 1; d <= remaining; d++) {
    cells.push({ date: new Date(year, month + 1, d), outOfMonth: true })
  }
  return cells
}

// ---------- component -------------------------------------------------------

export default function DatePicker({ value, onChange, disabled }) {
  const today = new Date()

  // Derive initial view state from value prop
  const initial = parseDDMMMYYYY(value) || today

  const [open,       setOpen]       = useState(false)
  const [viewYear,   setViewYear]   = useState(initial.getFullYear())
  const [viewMonth,  setViewMonth]  = useState(initial.getMonth())
  const [yearInput,  setYearInput]  = useState(String(initial.getFullYear()))
  const [editYear,   setEditYear]   = useState(false)
  const [alignRight, setAlignRight] = useState(false)

  const wrapRef    = useRef(null)
  const triggerRef = useRef(null)

  // FIX #3: recompute parsed inside the effect, don't close over outer `parsed`
  useEffect(() => {
    const parsed = parseDDMMMYYYY(value)
    if (parsed) {
      setViewYear(parsed.getFullYear())
      setViewMonth(parsed.getMonth())
      setYearInput(String(parsed.getFullYear()))
    }
  }, [value])

  // Close on outside click
  useEffect(() => {
    if (!open) return
    function handle(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [open])

  // FIX #5: detect if trigger is near the right edge and flip dropdown left
  const handleOpen = useCallback(() => {
    if (disabled) return
    if (!open && triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect()
      setAlignRight(rect.left > window.innerWidth / 2)
    }
    setOpen(o => !o)
  }, [disabled, open])

  function prevMonth() {
    if (viewMonth === 0) { setViewMonth(11); setViewYear(y => y - 1) }
    else setViewMonth(m => m - 1)
  }
  function nextMonth() {
    if (viewMonth === 11) { setViewMonth(0); setViewYear(y => y + 1) }
    else setViewMonth(m => m + 1)
  }

  function selectDate(date) {
    onChange(toDDMMMYYYY(date))
    setOpen(false)
  }

  function commitYearInput() {
    const y = parseInt(yearInput, 10)
    if (!isNaN(y) && y >= 1900 && y <= 2100) {
      setViewYear(y)
      setYearInput(String(y)) // FIX #2: sync input text to committed value
    } else {
      setYearInput(String(viewYear)) // revert on invalid
    }
    setEditYear(false)
  }

  const parsed      = parseDDMMMYYYY(value)
  const grid        = buildGrid(viewYear, viewMonth)
  const displayText = value || 'Select date…'
  const hasValue    = !!parsed

  return (
    <div className="dp-wrap" ref={wrapRef}>
      {/* ── Trigger ──────────────────────────────────────── */}
      <button
        ref={triggerRef}
        type="button"
        className={`dp-trigger${hasValue ? ' dp-trigger--set' : ''}${disabled ? ' dp-trigger--disabled' : ''}`}
        onClick={handleOpen}
        aria-haspopup="true"
        aria-expanded={open}
      >
        <span className="dp-cal-icon">📅</span>
        <span className="dp-trigger-text">{displayText}</span>
        <span className={`dp-chevron${open ? ' dp-chevron--open' : ''}`}>▾</span>
      </button>

      {/* ── Dropdown ─────────────────────────────────────── */}
      {open && (
        <div className={`dp-dropdown${alignRight ? ' dp-dropdown--right' : ''}`}>

          {/* Month / Year nav */}
          <div className="dp-nav">
            <button className="dp-nav-btn" onClick={prevMonth} title="Previous month">‹</button>

            <div className="dp-nav-center">
              <span className="dp-month-label">{MONTH_NAMES[viewMonth]}</span>

              {editYear ? (
                <input
                  className="dp-year-input"
                  value={yearInput}
                  autoFocus
                  onChange={e => setYearInput(e.target.value)}
                  onBlur={commitYearInput}
                  onKeyDown={e => {
                    if (e.key === 'Enter') commitYearInput()
                    if (e.key === 'Escape') { setYearInput(String(viewYear)); setEditYear(false) }
                  }}
                  size={5}
                />
              ) : (
                <button className="dp-year-btn" onClick={() => setEditYear(true)} title="Click to edit year">
                  {viewYear}
                </button>
              )}
            </div>

            <button className="dp-nav-btn" onClick={nextMonth} title="Next month">›</button>
          </div>

          {/* Day-of-week header */}
          <div className="dp-dow-row">
            {DOW.map(d => <span key={d} className="dp-dow">{d}</span>)}
          </div>

          {/* Date grid */}
          <div className="dp-grid">
            {grid.map(({ date, outOfMonth }, i) => {
              const isSelected = sameDay(date, parsed)
              const isToday    = sameDay(date, today)
              let cls = 'dp-cell'
              if (outOfMonth) cls += ' dp-cell--out'
              if (isSelected) cls += ' dp-cell--selected'
              if (isToday && !isSelected) cls += ' dp-cell--today'
              return (
                <button
                  key={i}
                  type="button"
                  className={cls}
                  onClick={() => !outOfMonth && selectDate(date)}
                  tabIndex={outOfMonth ? -1 : 0}
                >
                  {date.getDate()}
                </button>
              )
            })}
          </div>

          {/* Footer — quick "Today" */}
          <div className="dp-footer">
            <button
              className="dp-today-btn"
              onClick={() => { setViewYear(today.getFullYear()); setViewMonth(today.getMonth()) }}
            >
              Today
            </button>
            {hasValue && (
              <button className="dp-clear-btn" onClick={() => { onChange(''); setOpen(false) }}>
                Clear
              </button>
            )}
          </div>

        </div>
      )}
    </div>
  )
}