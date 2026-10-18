import type { Page, Route } from '@playwright/test'

/**
 * API simulada conforme specs/anexos/04/api-contract.md. Estado em memória,
 * por teste; `calls` registra o que o front enviou.
 */

export interface MockOptions {
  permissions?: Record<string, string[]>
  skillsEnabled?: boolean
  isMaster?: boolean
}

const ALL = ['view', 'create', 'edit', 'delete']
export const ADMIN_PERMS: Record<string, string[]> = {
  chats: ['view', 'create'],
  templates: ALL,
  skills: ALL,
  environment: ['view', 'manage'],
  users: ALL,
  user_profiles: ALL,
  datasources: ALL,
  files: ALL,
  usage: ['view'],
}

const AGENT_MD = '---\nname: atendimento\ndescription: Atende clientes\n---\n# Atendimento\n\nResponda com cordialidade.\n'
const NOW = '2026-10-15T12:00:00Z'

export function createState(opts: MockOptions = {}) {
  const state = {
    opts,
    calls: [] as { method: string; path: string; body: unknown }[],
    templates: [
      { id: 't-est', tenant_id: 'tn', name: 'Vendas', description: 'Time de vendas', agent_kind: 'structured', active_version_id: 'v1', active_version_number: 3, updated_at: NOW, system_key: null },
      { id: 't-sk', tenant_id: 'tn', name: 'Atendimento N1', description: 'Triagem', agent_kind: 'skills', active_version_id: 'sv2', active_version_number: 2, updated_at: NOW, system_key: null },
      { id: 't-draft', tenant_id: 'tn', name: 'Rascunho cobranca', description: '', agent_kind: 'skills', active_version_id: null, active_version_number: null, updated_at: NOW, system_key: null },
      { id: 't-sys', tenant_id: 'tn', name: 'Padrão da casa', description: '', agent_kind: 'structured', active_version_id: 'v9', active_version_number: 1, updated_at: NOW, system_key: 'default' },
    ] as Record<string, unknown>[],
    drafts: {} as Record<string, { files: { path: string; content: string; size_bytes: number }[]; manifest: unknown }>,
    versions: [
      { id: 'sv2', number: 2, notes: 'Primeira publicação', created_at: NOW, created_by: 'Ana', is_active: true },
      { id: 'sv1', number: 1, notes: '', created_at: NOW, created_by: 'Ana', is_active: false },
    ] as Record<string, unknown>[],
    userSkills: [
      { id: 'us1', name: 'u-cobranca-educada', description: 'Cobrança educada por WhatsApp', status: 'pending_review', source: 'agent', template_id: null, origin_chat_id: 'c1', revision: 1, created_at: NOW, updated_at: NOW, content: '---\nname: u-cobranca-educada\ndescription: Cobrança educada por WhatsApp\n---\nQuando o cliente atrasar, ...\n' },
      { id: 'us2', name: 'u-resumo-semanal', description: 'Resumo de pedidos da semana', status: 'active', source: 'user', template_id: null, origin_chat_id: null, revision: 2, created_at: NOW, updated_at: NOW, content: '---\nname: u-resumo-semanal\ndescription: Resumo\n---\nResuma.\n' },
    ] as Record<string, unknown>[],
    environment: {
      enabled: true,
      state: 'running',
      plan: 'Básico',
      usage: { cpu_pct: 12, ram_mb: 512, ram_limit_mb: 2048, disk_gb: 3, disk_limit_gb: 20, egress_gb: 1.5 },
      apps: [{ id: 'a1', name: 'Painel de vendas', url: 'https://painel.exemplo.net', status: 'online', port: 3000 }],
      domains: [] as Record<string, unknown>[],
      last_backup_at: NOW,
    } as Record<string, unknown>,
    chatSend: null as null | ((route: Route, body: Record<string, unknown>) => Promise<void> | void),
    validateProblems: [] as Record<string, unknown>[],
    approvals: [] as Record<string, unknown>[],
  }
  return state
}
export type MockState = ReturnType<typeof createState>

const json = (route: Route, data: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })

export function sse(events: [string, unknown][]): string {
  return events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join('')
}

export async function installMock(page: Page, state: MockState) {
  await page.addInitScript(() => localStorage.setItem('agent-platform.token', 'tok'))
  await page.route('**/api/**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const path = url.pathname.replace(/^\/api/, '')
    const method = req.method()
    let body: any = null
    try {
      body = req.postDataJSON()
    } catch {
      body = null
    }
    state.calls.push({ method, path, body })
    const m = (re: RegExp) => path.match(re)

    if (path === '/auth/me') {
      return json(route, {
        id: 'u1',
        email: 'ana@exemplo.net',
        name: 'Ana Souza',
        is_master: state.opts.isMaster ?? false,
        tenant_id: 'tn',
        permissions: state.opts.permissions ?? ADMIN_PERMS,
        branding: { name: 'Loja Exemplo', tenant_key: 'loja', has_logo: false, color: '#4f46e5', theme: 'dark' },
        skills_agents_enabled: state.opts.skillsEnabled ?? true,
      })
    }
    if (path === '/templates' && method === 'GET') return json(route, state.templates)
    if (path === '/templates' && method === 'POST') {
      const t = { id: `t-new-${state.templates.length}`, tenant_id: 'tn', name: body.name, description: body.description ?? '', agent_kind: body.agent_kind ?? 'structured', active_version_id: null, active_version_number: null, updated_at: new Date().toISOString(), system_key: null }
      state.templates.push(t)
      return json(route, t, 201)
    }
    let r: RegExpMatchArray | null
    if ((r = m(/^\/templates\/([^/]+)\/draft$/))) {
      const id = r[1]
      if (method === 'GET') {
        state.drafts[id] ??= {
          files: [
            { path: 'AGENTE.md', content: AGENT_MD, size_bytes: AGENT_MD.length },
            { path: 'skills/triagem/SKILL.md', content: '---\nname: triagem\ndescription: Quando usar triagem\n---\n# Triagem\n', size_bytes: 60 },
          ],
          manifest: { tools: ['consultar_pedidos'], environment: { enabled: false }, model: null, limits: {} },
        }
        return json(route, { ...state.drafts[id], updated_at: '2026-10-16T10:00:00Z', updated_by: 'Ana' })
      }
      state.drafts[id] = body
      return json(route, { ok: true, problems: [] })
    }
    if ((r = m(/^\/templates\/([^/]+)\/validate$/))) {
      const missing = !body.files.some((f: any) => f.path === 'AGENTE.md')
      const problems = missing
        ? [{ path: 'AGENTE.md', code: 'missing_agent_md', message: 'Falta o arquivo AGENTE.md', severity: 'error' }]
        : state.validateProblems
      return json(route, { ok: !problems.some((p: any) => p.severity === 'error'), problems })
    }
    if ((r = m(/^\/templates\/([^/]+)\/versions$/))) {
      if (method === 'GET') {
        if (r[1] === 't-est') return json(route, [{ id: 'v1', version_number: 3, notes: '', created_at: NOW }])
        return json(route, r[1] === 't-sk' ? state.versions : [])
      }
      const v = { id: `sv${state.versions.length + 1}`, number: state.versions.length + 1, notes: body.notes ?? '', created_at: new Date().toISOString(), created_by: 'Ana', is_active: false }
      state.versions.unshift(v)
      return json(route, v, 201)
    }
    if (m(/^\/templates\/t-est\/versions\/v1$/)) {
      return json(route, { id: 'v1', version_number: 3, supervisor_prompt: 'Você coordena vendas.', supervisor_ai_service_id: null, supervisor_model_override: null, supervisor_reasoning_effort: null, max_steps: 8, write_tables: [], require_write_confirmation: true, history_limit: 20, compress_history: true, tool_output_limit: 4000, notes: '', agents: [], datasource_ids: [] })
    }
    if ((r = m(/^\/templates\/([^/]+)\/versions\/([^/]+)\/files$/))) {
      return json(route, [{ path: 'AGENTE.md', size_bytes: 40, sha256: 'x' }])
    }
    if ((r = m(/^\/templates\/([^/]+)\/versions\/([^/]+)\/files\/(.+)$/))) {
      return json(route, { path: decodeURIComponent(r[3]), content: '# Versão antiga\n' })
    }
    if ((r = m(/^\/templates\/([^/]+)\/deploy$/))) {
      state.versions = state.versions.map((v) => ({ ...v, is_active: v.id === body.version_id }))
      return json(route, { ok: true })
    }
    if (path === '/tools/catalog') {
      return json(route, [
        { id: 'consultar_pedidos', label: 'Consultar pedidos', description: 'Lê pedidos', sensitive: false },
        { id: 'enviar_email', label: 'Enviar e-mail', description: 'Envia mensagens', sensitive: true },
      ])
    }
    if (path === '/skills/company') {
      return json(route, [{ template_id: 't-sk', template_name: 'Atendimento N1', name: 'triagem', description: 'Quando usar triagem', updated_at: NOW }])
    }
    if (path === '/me/skills') {
      const st = url.searchParams.get('status')
      return json(route, state.userSkills.filter((s) => !st || s.status === st))
    }
    if ((r = m(/^\/me\/skills\/([^/]+)\/(approve|discard|disable|enable)$/))) {
      const s = state.userSkills.find((x) => x.id === r![1])!
      if (r[2] === 'discard') state.userSkills = state.userSkills.filter((x) => x.id !== s.id)
      else s.status = r[2] === 'approve' || r[2] === 'enable' ? 'active' : 'disabled'
      return json(route, s)
    }
    if ((r = m(/^\/me\/skills\/([^/]+)$/))) {
      const s = state.userSkills.find((x) => x.id === r![1])!
      if (method === 'GET') return json(route, s)
      if (method === 'PUT') {
        Object.assign(s, body)
        return json(route, s)
      }
      state.userSkills = state.userSkills.filter((x) => x.id !== s.id)
      return route.fulfill({ status: 204 })
    }
    if (path === '/approvals/pending') return json(route, state.approvals)
    if (m(/^\/approvals\/([^/]+)$/)) return json(route, { status: body.decision === 'approve' ? 'approved' : 'denied' })
    if (path === '/environment') return json(route, state.environment)
    if ((r = m(/^\/environment\/(start|stop|backup)$/))) {
      if (r[1] !== 'backup') state.environment.state = r[1] === 'start' ? 'running' : 'stopped'
      return json(route, { state: state.environment.state })
    }
    if (path === '/environment/domains' && method === 'POST') {
      const d = { id: 'd1', host: body.host, status: 'pending_verification', txt_name: `_verificacao.${body.host}`, txt_value: 'abc123' }
      ;(state.environment.domains as unknown[]).push(d)
      return json(route, d, 201)
    }
    if (m(/^\/environment\/domains\/([^/]+)$/)) {
      state.environment.domains = []
      return route.fulfill({ status: 204 })
    }
    if (path === '/chats' && method === 'GET') {
      return json(route, [{ id: 'c1', title: 'Conversa de teste', template_id: null, agent_kind: 'structured', created_at: NOW, updated_at: NOW }])
    }
    if (path === '/chats/c1/messages') return json(route, [])
    if (path === '/chat/send') {
      if (state.chatSend) return state.chatSend(route, body)
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sse([['chat', { chat_id: 'c1', run_id: 'r1' }], ['token', { text: 'Olá!' }], ['done', { text: 'Olá!' }]]),
      })
    }
    if (path.startsWith('/chat/runs/')) return json(route, { status: 'cancelling' })
    // Qualquer outra leitura devolve lista vazia para a tela não quebrar.
    if (method === 'GET') return json(route, [])
    return json(route, { ok: true })
  })
}
