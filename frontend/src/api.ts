const configuredBase = (import.meta.env.VITE_API_URL || '/api').replace(/\/+$/, '')
const base = configuredBase.endsWith('/api') ? configuredBase : `${configuredBase}/api`

export function apiUrl(path: string): string {
  return `${base}${path.startsWith('/') ? path : `/${path}`}`
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers)
  const response = await fetch(apiUrl(path), {...options, headers})
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

export const json = (method: string, body: unknown): RequestInit => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
