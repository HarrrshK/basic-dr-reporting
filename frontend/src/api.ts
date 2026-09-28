const base = import.meta.env.VITE_API_URL || '/api'
const API_TOKEN_KEY = 'field-reports-api-token'

export function getApiToken(): string {
  return window.localStorage.getItem(API_TOKEN_KEY) || ''
}

export function setApiToken(token: string): void {
  if (token) window.localStorage.setItem(API_TOKEN_KEY, token.trim())
  else window.localStorage.removeItem(API_TOKEN_KEY)
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers)
  const token = getApiToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${base}${path}`, {...options, headers})
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

export const json = (method: string, body: unknown): RequestInit => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
