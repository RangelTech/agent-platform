import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Badge, Button, Card, EmptyState, ErrorText, Input, PageHeader, SectionIntro, Select, StatCard, Table, TableSkeleton } from '../components/ui'
import { ContasIA } from '../components/ContasIA'
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

  const { data: services = [], isLoading, error: loadError } = useQuery({
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

  const archive = useMutation({
    mutationFn: (id: string) => api(`/ai-services/${id}`, { method: 'DELETE' }),
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

  const activeCount = services.filter((service) => service.is_active).length
  const healthyCount = services.filter((service) => service.last_test_ok).length

  return (
    <div className="space-y-6">
      <PageHeader
        title="Serviços de IA"
        description="Conecte provedores por tenant, mantenha chaves sob seu controle e monitore rapidamente disponibilidade e saúde operacional."
        actions={
          isMaster ? (
            <Button type="submit" form="ai-service-form" disabled={create.isPending}>
              {create.isPending ? 'Salvando…' : 'Novo serviço'}
            </Button>
          ) : null
        }
      />

      {/* Contas da empresa e combos: o caminho novo, em que o cliente traz as
          contas dele. O cadastro direto de provider continua abaixo. */}
      {!isMaster && <ContasIA />}

      <div className="grid gap-4 md:grid-cols-3">
        <StatCard label="Serviços totais" value={String(services.length)} meta="Catálogo consolidado por tenant e provider." />
        <StatCard label="Serviços ativos" value={String(activeCount)} meta="Prontos para uso em supervisores e especialistas." />
        <StatCard label="Últimos testes OK" value={String(healthyCount)} meta="Resultado mais recente persistido no backend." />
      </div>

      {isMaster && (
      <Card title="Cadastrar serviço BYOK">
        <div className="space-y-6">
          <SectionIntro
            eyebrow="Model providers"
            title="Configure o provider, modelo e escopo do tenant"
            description="Cada serviço pode abastecer templates diferentes. Prefira nomes claros para facilitar seleção em supervisores, especialistas e homologações."
          />
          <form id="ai-service-form" onSubmit={onSubmit} className="grid gap-4 lg:grid-cols-2">
            <Input
              label="Nome interno"
              hint="Ex.: Gemini produção, Claude homolog ou OpenAI financeiro."
              required
              name="svc-name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
            <Select
              label="Provider"
              hint="Escolha o vendor compatível com a chave que será usada."
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
              label="Modelo padrão"
              hint="Esse modelo aparecerá como default na criação de templates."
              required
              name="svc-model"
              value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
            />
            <Input
              label="API key"
              hint="A chave é enviada criptografada ao backend e não volta em listagens."
              type="password"
              required
              name="svc-key"
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
            />
            {form.provider === 'openai-compatible' && (
              <Input
                label="Base URL"
                hint="Obrigatório para gateways compatíveis com OpenAI."
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
                hint="Defina o tenant proprietário desse serviço."
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
            <div className="lg:col-span-2">
              <ErrorText>{error}</ErrorText>
            </div>
          </form>
        </div>
      </Card>
      )}


      <Card title="Serviços cadastrados" actions={<Badge ok={activeCount > 0}>{activeCount} ativos</Badge>}>
        {isLoading ? (
          <TableSkeleton columns={6} />
        ) : loadError ? (
          <ErrorText>Não foi possível carregar os dados. Recarregue a página ou tente novamente.</ErrorText>
        ) : services.length === 0 ? (
          <EmptyState
            title="Nenhum serviço de IA cadastrado"
            description="Cadastre uma chave BYOK acima para que os templates deste tenant possam chamar um modelo."
          />
        ) : (
          <Table headers={['Serviço', 'Provider', 'Modelo', 'Teste', 'Status', 'Ações']}>
            {services.map((s) => (
              <tr key={s.id} data-testid="service-row" className="transition hover:bg-[var(--brand-soft)]/40">
                <td className="px-4 py-4 text-[var(--text)]">
                  <div>
                    <p className="font-medium">{s.name}</p>
                    <p className="mt-1 text-sm text-[var(--text-muted)]">{s.has_key ? 'Chave configurada e pronta para uso.' : 'Sem chave salva.'}</p>
                  </div>
                </td>
                <td className="px-4 py-4 text-[var(--text-muted)]">{s.provider}</td>
                <td className="px-4 py-4 font-mono text-xs text-[var(--text-muted)]">{s.model}</td>
                <td className="px-4 py-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Button variant="ghost" onClick={() => test.mutate(s.id)} disabled={test.isPending}>
                      Testar
                    </Button>
                    {testResult[s.id] ? (
                      <span className="text-xs text-[var(--text-muted)]">{testResult[s.id]}</span>
                    ) : (
                      s.last_test_ok !== null && (
                        <Badge ok={s.last_test_ok}>{s.last_test_ok ? 'OK' : 'Falhou'}</Badge>
                      )
                    )}
                  </div>
                </td>
                <td className="px-4 py-4">
                  <Badge ok={s.is_active}>{s.is_active ? 'Ativo' : 'Inativo'}</Badge>
                </td>
                <td className="px-4 py-4 text-right">
                  <div className="flex justify-end gap-2">
                    <Button
                      variant="ghost"
                      onClick={() => toggle.mutate({ id: s.id, is_active: !s.is_active })}
                    >
                      {s.is_active ? 'Desativar' : 'Ativar'}
                    </Button>
                    <Button variant="ghost" onClick={() => archive.mutate(s.id)}>
                      Arquivar
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  )
}
