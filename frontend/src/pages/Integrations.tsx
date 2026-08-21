import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
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
            hint="Credencial de API para integrar outro sistema."
            name="int-channel"
            value={form.channel}
            onChange={(e) => setForm({ ...form, channel: e.target.value })}
          >
            <option value="api">API (máquina a máquina)</option>
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
              <td className="px-3 py-2 text-[var(--text-muted)]">API</td>
              <td className="px-3 py-2 font-mono text-xs text-[var(--text-muted)]">{i.key_prefix}</td>
              <td className="px-3 py-2 text-xs text-[var(--text-muted)]">{i.webhook_url ?? '—'}</td>
              <td className="px-3 py-2">
                <Badge ok={i.is_active}>{i.is_active ? 'Ativa' : 'Revogada'}</Badge>
              </td>
              <td className="px-3 py-2 text-right">
                <div className="flex justify-end gap-2">
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
    </div>
  )
}

