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
  /** Corpo JSON completo do erro (ex.: lista de problemas de validação). */
  body: unknown

  constructor(status: number, message: string, body: unknown = null) {
    super(message)
    this.status = status
    this.body = body
  }
}

function apiErrorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (!item || typeof item !== 'object') return null
        const issue = item as { loc?: unknown; msg?: unknown }
        const field = Array.isArray(issue.loc) ? issue.loc.at(-1) : null
        const message = typeof issue.msg === 'string' ? issue.msg : null
        return message ? `${field ? `${field}: ` : ''}${message}` : null
      })
      .filter((message): message is string => Boolean(message))
    if (messages.length) return messages.join('. ')
  }
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string') return message
  }
  return fallback
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
    // A failed login is not an expired session. Preserve the API's actionable
    // message instead of emitting the global session-expired event.
    if (!token) {
      const detail = await res
        .json()
        .then((body) => body.detail)
        .catch(() => null)
      throw new ApiError(401, apiErrorMessage(detail, 'E-mail ou senha incorretos'))
    }
    // The session is gone; drop it so the app falls back to the login screen.
    setToken(null)
    window.dispatchEvent(new Event('auth:expired'))
    throw new ApiError(401, 'Sessão expirada')
  }

  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = body && typeof body === 'object' ? (body as { detail?: unknown }).detail : null
    throw new ApiError(res.status, apiErrorMessage(detail, `Erro ${res.status}`), body)
  }

  return res.status === 204 ? (undefined as T) : res.json()
}

export interface Branding {
  name: string
  tenant_key: string
  has_logo: boolean
  color: string
  theme: 'dark' | 'light'
}

export interface Me {
  id: string
  email: string
  name: string
  is_master: boolean
  tenant_id: string | null
  permissions: Record<string, string[]>
  branding: Branding
  /** Empresa com agentes por skills habilitados (serializado pelo servidor). */
  skills_agents_enabled?: boolean
}

export interface Tenant {
  id: string
  tenant_key: string
  name: string
  is_active: boolean
  router_provisioning_status?: 'pending' | 'provisioning' | 'ready' | 'failed'
  router_provisioning_error?: string | null
}

export interface TenantPage {
  items: Tenant[]
  total: number
  active_total: number
  page: number
  page_size: number
  total_pages: number
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
  api<{ token: string; chatwoot_sso_url: string | null }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })

export const fetchMe = () => api<Me>('/auth/me')
export const heartbeat = () => api<void>('/auth/heartbeat', { method: 'POST' })
export const logout = () => api<void>('/auth/logout', { method: 'POST' })

export const listTenantPage = (q = '', page = 1, pageSize = 25) =>
  api<TenantPage>(`/tenants?q=${encodeURIComponent(q)}&page=${page}&page_size=${pageSize}`)
// Existing tenant selectors only need a compact option list. The master
// companies screen uses listTenantPage so it never renders the whole base.
export const listTenants = async () => (await listTenantPage('', 1, 100)).items
export const createTenant = (body: {
  name: string
  tenant_key: string
  admin_name?: string | null
  admin_email?: string | null
  admin_password?: string | null
}) => api<Tenant>('/tenants', { method: 'POST', body: JSON.stringify(body) })
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
