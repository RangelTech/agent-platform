import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Badge, Button, Card, ErrorText, Input, Select, Table } from '../components/ui'
import { api, listTenants } from '../lib/api'
import { useAuth } from '../lib/auth'

interface AiService {
  id: string
  tenant_id: string
  name: string
  provider: string
  model: string
  auth_type: string
  api_base: string | null
  is_active: boolean
  has_key: boolean
  last_test_at: string | null
  last_test_ok: boolean | null
}

const PROVIDERS = [
  { value: 'gemini', label: 'Google Gemini', model: 'gemini-flash-latest' },
  { value: 'openai', label: 'OpenAI', model: 'gpt-4o-mini' },
  { value: 'anthropic', label: 'Anthropic Claude', model: 'claude-sonnet-5' },
  { value: 'deepseek', label: 'DeepSeek', model: 'deepseek-chat' },
  { value: 'groq', label: 'Groq', model: 'llama-3.3-70b-versatile' },
  { value: 'openai-compatible', label: 'OpenAI-compatible (URL própria)', model: '' },
]

export default function AiServices() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const isMaster = user?.is_master ?? false

  const { data: services = [], isLoading } = useQuery({
    queryKey: ['ai-services'],
    queryFn: () => api<AiService[]>('/ai-services'),
  })
  const { data: tenants = [] } = useQuery({
    queryKey: ['tenants'],
    queryFn: listTenants,
    enabled: isMaster,
  })

  const [form, setForm] = useState({
    name: '',
    provider: 'gemini',
    model: 'gemini-flash-latest',
    api_key: '',
    api_base: '',
    tenant_id: '',
  })
  const [error, setError] = useState('')
  const [testResult, setTestResult] = useState<Record<string, string>>({})

  const create = useMutation({
    mutationFn: () =>
      api<AiService>('/ai-services', {
        method: 'POST',
        body: JSON.stringify({
          name: form.name,
          provider: form.provider,
          model: form.model,
          api_key: form.api_key,
          api_base: form.api_base || null,
          tenant_id: isMaster ? form.tenant_id || null : null,
        }),
      }),
    onSuccess: () => {
      setForm({ ...form, name: '', api_key: '' })
      setError('')
      qc.invalidateQueries({ queryKey: ['ai-services'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  const test = useMutation({
    mutationFn: (id: string) =>
      api<{ ok: boolean; detail: string }>(`/ai-services/${id}/test`, { method: 'POST' }),
    onSuccess: (result, id) => {
      setTestResult((t) => ({
        ...t,
        [id]: result.ok ? 'Conexão OK' : `Falhou: ${result.detail.slice(0, 120)}`,
      }))
      qc.invalidateQueries({ queryKey: ['ai-services'] })
    },
  })

  const toggle = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      api<AiService>(`/ai-services/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ is_active }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ai-services'] }),
  })

  function pickProvider(value: string) {
    const p = PROVIDERS.find((p) => p.value === value)
    setForm({ ...form, provider: value, model: p?.model ?? '' })
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    create.mutate()
  }

  return (
    <div className="space-y-6">
      <Card title="Novo serviço de IA (traga sua chave)">
        <form onSubmit={onSubmit} className="grid gap-3 sm:grid-cols-2">
          <Input
            label="Nome"
            required
            name="svc-name"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <Select
            label="Provider"
            name="svc-provider"
            value={form.provider}
            onChange={(e) => pickProvider(e.target.value)}
          >
            {PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </Select>
          <Input
            label="Modelo"
            required
            name="svc-model"
            value={form.model}
            onChange={(e) => setForm({ ...form, model: e.target.value })}
          />
          <Input
            label="API key"
            type="password"
            required
            name="svc-key"
            value={form.api_key}
            onChange={(e) => setForm({ ...form, api_key: e.target.value })}
          />
          {form.provider === 'openai-compatible' && (
            <Input
              label="Base URL"
              required
              name="svc-base"
              placeholder="https://minha-api.com/v1"
              value={form.api_base}
              onChange={(e) => setForm({ ...form, api_base: e.target.value })}
            />
          )}
          {isMaster && (
            <Select
              label="Empresa"
              required
              name="svc-tenant"
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
              Cadastrar serviço
            </Button>
            <ErrorText>{error}</ErrorText>
          </div>
        </form>
      </Card>

      <Card title="Serviços de IA">
        {isLoading ? (
          <p className="text-sm text-slate-400">Carregando…</p>
        ) : (
          <Table headers={['Nome', 'Provider', 'Modelo', 'Teste', 'Status', '']}>
            {services.map((s) => (
              <tr key={s.id} data-testid="service-row">
                <td className="px-3 py-2 text-slate-200">{s.name}</td>
                <td className="px-3 py-2 text-slate-400">{s.provider}</td>
                <td className="px-3 py-2 font-mono text-xs text-slate-400">{s.model}</td>
                <td className="px-3 py-2">
                  <div className="flex items-center gap-2">
                    <Button variant="ghost" onClick={() => test.mutate(s.id)} disabled={test.isPending}>
                      Testar
                    </Button>
                    {testResult[s.id] ? (
                      <span className="text-xs text-slate-400">{testResult[s.id]}</span>
                    ) : (
                      s.last_test_ok !== null && (
                        <Badge ok={s.last_test_ok}>{s.last_test_ok ? 'OK' : 'Falhou'}</Badge>
                      )
                    )}
                  </div>
                </td>
                <td className="px-3 py-2">
                  <Badge ok={s.is_active}>{s.is_active ? 'Ativo' : 'Inativo'}</Badge>
                </td>
                <td className="px-3 py-2 text-right">
                  <Button
                    variant="ghost"
                    onClick={() => toggle.mutate({ id: s.id, is_active: !s.is_active })}
                  >
                    {s.is_active ? 'Desativar' : 'Ativar'}
                  </Button>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  )
}
