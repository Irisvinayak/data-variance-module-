/**
 * Build-time configuration shared by the API client and the auth bootstrap.
 *
 * Empty in dev — the Vite proxy forwards backend routes from the same origin.
 * Set VITE_API_BASE_URL (e.g. /DataVar/api) for reverse-proxy deployments; it
 * must match the IIS virtual directory and the web.config rewrite prefix.
 */
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
