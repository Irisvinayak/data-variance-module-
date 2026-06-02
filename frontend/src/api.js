/**
 * HTTP helpers for the standalone Data Variance application.
 * All requests go to the FastAPI backend (proxied via Vite on /variance).
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

export async function findReturnTables(returnName) {
  const res = await fetch(
    `${BASE_URL}/variance/find?return_name=${encodeURIComponent(returnName)}`
  )
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `Find error (${res.status})`)
  }
  return res.json()
}

export async function computeVariance(payload) {
  const res = await fetch(`${BASE_URL}/variance/compute`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(payload),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `Compute error (${res.status})`)
  }
  return res.json()
}
