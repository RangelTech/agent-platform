const TOKEN_KEY = 'agent-platform.token'
/** Marca (na sessão do navegador) que o login caiu, para a tela de entrada avisar. */
export const SESSION_EXPIRED_KEY = 'agent-platform.session-expired'

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

const FIELD_LABEL: Record<string, string> = {
  name: 'Nome',
  description: 'Descrição',
  email: 'E-mail',
  password: 'Senha',
  title: 'Título',
  content: 'Conteúdo',
  path: 'Caminho',
}

/** Traduz as mensagens padrão de validação do servidor (pydantic) para pt-BR. */
function translateValidation(msg: string): string {
  let m = /^String should have at most (\d+) characters?$/.exec(msg)
  if (m) return `deve ter no máximo ${m[1]} caracteres`
  m = /^String should have at least (\d+) characters?$/.exec(msg)
  if (m) return `deve ter pelo menos ${m[1]} caracteres`
  m = /^Input should be greater than or equal to (\d+)/.exec(msg)
  if (m) return `deve ser no mínimo ${m[1]}`
  m = /^Input should be less than or equal to (\d+)/.exec(msg)
  if (m) return `deve ser no máximo ${m[1]}`
  if (msg === 'Field required') return 'campo obrigatório'
  if (/valid email/i.test(msg)) return 'e-mail inválido'
  if (/^Input should be/.test(msg) || /^Value error/.test(msg)) return 'valor inválido'
  return msg
}

/** Mensagem em pt-BR quando o servidor não explica o erro. */
export function statusMessage(status: number): string {
  if (status === 403) return 'Você não tem permissão para esta ação.'
  if (status === 404) return 'Não encontramos o que você procurou.'
  if (status === 409) return 'Esta alteração entrou em conflito com outra. Recarregue a página e tente de novo.'
  if (status === 413) return 'O conteúdo enviado é grande demais.'
  if (status === 429) return 'Muitas tentativas seguidas. Aguarde um instante e tente de novo.'
  if (status >= 500) return 'O servidor teve um problema. Tente de novo em instantes.'
  return `Não foi possível concluir (erro ${status}).`
}

const GENERIC_DETAIL = /^(Internal Server Error|Not Found|Forbidden|Unauthorized|Bad Request|Bad Gateway|Service Unavailable|Gateway Timeout|Conflict)\.?$/i

function apiErrorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return GENERIC_DETAIL.test(detail.trim()) ? fallback : detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (!item || typeof item !== 'object') return null
        const issue = item as { loc?: unknown; msg?: unknown }
        const rawField = Array.isArray(issue.loc) ? issue.loc.at(-1) : null
        const field = typeof rawField === 'string' ? (FIELD_LABEL[rawField] ?? rawField) : rawField
        const message = typeof issue.msg === 'string' ? translateValidation(issue.msg) : null
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
  let res: Response
  try {
    res = await fetch(`/api${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options.headers,
      },
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError(0, 'Sem conexão com o servidor. Confira a internet e tente de novo.')
  }

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
    throw new ApiError(res.status, apiErrorMessage(detail, statusMessage(res.status)), body)
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
  graph_agents_enabled?: boolean
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
