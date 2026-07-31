import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent } from 'react'
import { Badge, Button, Card, ErrorText, Input, PageHeader, Select, Table } from '../components/ui'
import { api, listTenants } from '../lib/api'
import { useAuth } from '../lib/auth'
import { listTemplates } from '../lib/templates'

interface Integration {
  id: string
  tenant_id: string
  name: string
  channel: string
  template_id: string | null
  key_prefix: string
  webhook_url: string | null
  rate_limit_per_minute: number
  is_active: boolean
  created_at: string
  api_key?: string
  webhook_secret?: string
}

export default function Integrations() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const isMaster = user?.is_master ?? false

  const { data: integrations = [] } = useQuery({
    queryKey: ['integrations'],
    queryFn: () => api<Integration[]>('/integrations'),
  })
  const { data: templates = [] } = useQuery({ queryKey: ['templates'], queryFn: listTemplates })
  const { data: tenants = [] } = useQuery({
    queryKey: ['tenants'],
    queryFn: listTenants,
    enabled: isMaster,
  })

  const [form, setForm] = useState({
    name: '',
    channel: 'api',
    template_id: '',
    webhook_url: '',
    tenant_id: '',
  })
  const [error, setError] = useState('')
  const [revealed, setRevealed] = useState<Integration | null>(null)
  const [whatsapp, setWhatsapp] = useState<Integration | null>(null)

  const create = useMutation({
    mutationFn: () =>
      api<Integration>('/integrations', {
        method: 'POST',
        body: JSON.stringify({
          name: form.name,
          channel: form.channel,
          template_id: form.template_id || null,
          webhook_url: form.webhook_url || null,
          tenant_id: isMaster ? form.tenant_id || null : null,
        }),
      }),
    onSuccess: (created) => {
      setRevealed(created)
      setForm({ name: '', channel: 'api', template_id: '', webhook_url: '', tenant_id: '' })
      setError('')
      qc.invalidateQueries({ queryKey: ['integrations'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  const rotate = useMutation({
    mutationFn: (id: string) =>
      api<{ api_key: string }>(`/integrations/${id}/rotate`, { method: 'POST' }),
    onSuccess: (r, id) => {
      setRevealed({ ...(integrations.find((i) => i.id === id) as Integration), api_key: r.api_key })
      qc.invalidateQueries({ queryKey: ['integrations'] })
    },
  })

  const revoke = useMutation({
    mutationFn: (id: string) => api(`/integrations/${id}/revoke`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['integrations'] }),
  })

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    create.mutate()
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Integrações" description="Gere credenciais de API para uso externo (webhooks, apps)." />

      {revealed?.api_key && (
        <Card title="Guarde estas credenciais — não serão mostradas de novo">
          <div className="space-y-2 font-mono text-sm">
            <p className="text-[var(--success)]" data-testid="revealed-key">
              API key: {revealed.api_key}
            </p>
            {revealed.webhook_secret && (
              <p className="text-[var(--info)]">Webhook secret: {revealed.webhook_secret}</p>
            )}
          </div>
          <p className="mt-3 text-xs text-[var(--text-muted)]">
            Use no header: <code>Authorization: Bearer &lt;API key&gt;</code> em{' '}
            <code>POST /v1/messages</code> — modos sync, stream (SSE) ou webhook.
          </p>
          <Button className="mt-3" variant="ghost" onClick={() => setRevealed(null)}>
            Já guardei
          </Button>
        </Card>
      )}

      <Card title="Nova integração">
        <form onSubmit={onSubmit} className="grid gap-3 sm:grid-cols-2">
          <Input
            label="Nome do sistema consumidor"
            required
            name="int-name"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <Select
            label="Canal"
            hint="API para integrar outro sistema; WhatsApp para atender pelo número da empresa."
            name="int-channel"
            value={form.channel}
            onChange={(e) => setForm({ ...form, channel: e.target.value })}
          >
            <option value="api">API (máquina a máquina)</option>
            <option value="whatsapp">WhatsApp (W-API)</option>
          </Select>
          <Select
            label="Template padrão"
            name="int-template"
            value={form.template_id}
            onChange={(e) => setForm({ ...form, template_id: e.target.value })}
          >
            <option value="">Padrão do tenant</option>
            {templates
              .filter((t) => t.active_version_id)
              .map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
          </Select>
          <Input
            label="Webhook URL (para respostas assíncronas, opcional)"
            name="int-webhook"
            placeholder="https://meusistema.com/callback"
            value={form.webhook_url}
            onChange={(e) => setForm({ ...form, webhook_url: e.target.value })}
          />
          {isMaster && (
            <Select
              label="Empresa"
              required
              value={form.tenant_id}
              onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
            >
              <option value="">Selecione…</option>
              {tenants.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </Select>
          )}
          <div className="sm:col-span-2 flex items-center gap-3">
            <Button type="submit" disabled={create.isPending}>
              Criar integração
            </Button>
            <ErrorText>{error}</ErrorText>
          </div>
        </form>
      </Card>

      <Card title="Todas as integrações">
        <Table headers={['Nome', 'Canal', 'Chave', 'Webhook', 'Status', '']}>
          {integrations.map((i) => (
            <tr key={i.id} data-testid="integration-row" className="transition hover:bg-[var(--brand-soft)]">
              <td className="px-3 py-2 text-[var(--text)]">{i.name}</td>
              <td className="px-3 py-2 text-[var(--text-muted)]">
                {i.channel === 'whatsapp' ? 'WhatsApp' : 'API'}
              </td>
              <td className="px-3 py-2 font-mono text-xs text-[var(--text-muted)]">{i.key_prefix}</td>
              <td className="px-3 py-2 text-xs text-[var(--text-muted)]">{i.webhook_url ?? '—'}</td>
              <td className="px-3 py-2">
                <Badge ok={i.is_active}>{i.is_active ? 'Ativa' : 'Revogada'}</Badge>
              </td>
              <td className="px-3 py-2 text-right">
                <div className="flex justify-end gap-2">
                  {i.channel === 'whatsapp' && (
                    <Button variant="ghost" onClick={() => setWhatsapp(i)}>
                      Conexão WhatsApp
                    </Button>
                  )}
                  <Button variant="ghost" onClick={() => rotate.mutate(i.id)}>
                    Rotacionar chave
                  </Button>
                  {i.is_active && (
                    <Button variant="danger" onClick={() => revoke.mutate(i.id)}>
                      Revogar
                    </Button>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </Table>
      </Card>

      {whatsapp && <WhatsappPanel integration={whatsapp} onClose={() => setWhatsapp(null)} />}
    </div>
  )
}

interface WhatsappConnection {
  id: string
  instance_id: string
  api_base: string
  has_token: boolean
  is_active: boolean
  last_test_ok: boolean | null
  webhook_path: string
}

/** Conexão da instância W-API que atende esta integração. */
function WhatsappPanel({ integration, onClose }: { integration: Integration; onClose: () => void }) {
  const qc = useQueryClient()
  const { data: connection } = useQuery({
    queryKey: ['whatsapp', integration.id],
    queryFn: async () => {
      try {
        return await api<WhatsappConnection>(`/integrations/${integration.id}/whatsapp`)
      } catch {
        // 404 = ainda não configurada; o formulário abre em branco.
        return null
      }
    },
  })

  const [instanceId, setInstanceId] = useState('')
  const [token, setToken] = useState('')
  const [apiBase, setApiBase] = useState('https://api.w-api.app/v1')
  const [error, setError] = useState('')
  const [testResult, setTestResult] = useState('')

  useEffect(() => {
    if (connection) {
      setInstanceId(connection.instance_id)
      setApiBase(connection.api_base)
    }
  }, [connection])

  const save = useMutation({
    mutationFn: () =>
      api<WhatsappConnection>(`/integrations/${integration.id}/whatsapp`, {
        method: 'PUT',
        body: JSON.stringify({
          instance_id: instanceId,
          token: token || undefined,
          api_base: apiBase,
          is_active: true,
        }),
      }),
    onSuccess: () => {
      setToken('')
      setError('')
      qc.invalidateQueries({ queryKey: ['whatsapp', integration.id] })
    },
    onError: (e: Error) => setError(e.message),
  })

  const test = useMutation({
    mutationFn: () =>
      api<{ ok: boolean; detail: string }>(`/integrations/${integration.id}/whatsapp/test`, {
        method: 'POST',
      }),
    onSuccess: (r) => setTestResult(r.ok ? 'Conexão OK' : `Falhou: ${r.detail}`),
    onError: (e: Error) => setTestResult(`Falhou: ${e.message}`),
  })

  const webhookUrl = `${window.location.origin}/webhooks/whatsapp/${integration.id}`

  return (
    <Card
      title={`Conexão WhatsApp — ${integration.name}`}
      actions={
        <Button variant="ghost" onClick={onClose}>
          Fechar
        </Button>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
        className="grid gap-3 sm:grid-cols-2"
      >
        <Input
          label="Instance ID"
          required
          name="wa-instance"
          value={instanceId}
          onChange={(e) => setInstanceId(e.target.value)}
        />
        <Input
          label="Token da instância"
          hint={connection?.has_token ? 'Já salvo. Preencha só para substituir.' : 'Token da W-API.'}
          type="password"
          name="wa-token"
          autoComplete="off"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        <Input
          label="URL base da W-API"
          name="wa-base"
          value={apiBase}
          onChange={(e) => setApiBase(e.target.value)}
        />
        <div className="flex items-end gap-3">
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? 'Salvando…' : 'Salvar conexão'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            disabled={!connection || test.isPending}
            onClick={() => test.mutate()}
          >
            Testar conexão
          </Button>
        </div>
        <div className="sm:col-span-2 space-y-3">
          {testResult && <p className="text-sm text-[var(--text-muted)]">{testResult}</p>}
          <ErrorText>{error}</ErrorText>
          <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface-soft)] p-4">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-faint)]">
              URL de webhook
            </p>
            <p className="mt-2 break-all font-mono text-xs text-[var(--text-muted)]">{webhookUrl}</p>
            <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">
              Cadastre esta URL no painel da W-API. As conversas recebidas aparecem no Chat com o
              selo WhatsApp, em modo leitura.
            </p>
          </div>
        </div>
      </form>
    </Card>
  )
}
