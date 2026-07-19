const TOKEN_KEY = 'agent-platform.token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getToken()
  const res = await fetch(`/api${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  })

  if (res.status === 401) {
    // The session is gone; drop it so the app falls back to the login screen.
    setToken(null)
    window.dispatchEvent(new Event('auth:expired'))
    throw new ApiError(401, 'Sessão expirada')
  }

  if (!res.ok) {
    const detail = await res
      .json()
      .then((b) => b.detail)
      .catch(() => null)
    throw new ApiError(res.status, detail ?? `Erro ${res.status}`)
  }

  return res.status === 204 ? (undefined as T) : res.json()
}

export interface Me {
  id: string
  email: string
  name: string
  is_master: boolean
  tenant_id: string | null
  permissions: Record<string, string[]>
}

export interface Tenant {
  id: string
  tenant_key: string
  name: string
  is_active: boolean
}

export interface Profile {
  id: string
  tenant_id: string | null
  name: string
  permissions: Record<string, string[]>
  is_active: boolean
}

export interface User {
  id: string
  tenant_id: string | null
  profile_id: string | null
  email: string
  name: string
  is_master: boolean
  is_active: boolean
}

export const login = (email: string, password: string) =>
  api<{ token: string }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })

export const fetchMe = () => api<Me>('/auth/me')
export const logout = () => api<void>('/auth/logout', { method: 'POST' })

export const listTenants = () => api<Tenant[]>('/tenants')
export const createTenant = (body: { name: string; tenant_key: string }) =>
  api<Tenant>('/tenants', { method: 'POST', body: JSON.stringify(body) })
export const updateTenant = (id: string, body: Partial<Tenant>) =>
  api<Tenant>(`/tenants/${id}`, { method: 'PUT', body: JSON.stringify(body) })

export const listProfiles = () => api<Profile[]>('/user-profiles')
export const listUsers = () => api<User[]>('/users')
export const createUser = (body: {
  email: string
  name: string
  password: string
  profile_id?: string | null
  tenant_id?: string | null
}) => api<User>('/users', { method: 'POST', body: JSON.stringify(body) })
export const updateUser = (id: string, body: Record<string, unknown>) =>
  api<User>(`/users/${id}`, { method: 'PUT', body: JSON.stringify(body) })
