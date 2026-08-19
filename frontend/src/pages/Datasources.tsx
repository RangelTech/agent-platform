import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Badge, Button, Card, EmptyState, ErrorText, Input, PageHeader, SectionIntro, Select, StatCard, Table, TableSkeleton, Textarea } from '../components/ui'
import { api, listTenants } from '../lib/api'
import { useAuth } from '../lib/auth'

export interface Datasource {
  id: string
  tenant_id: string
  name: string
  kind: string
  config: Record<string, unknown>
  has_secret: boolean
  is_active: boolean
  last_test_at: string | null
  last_test_ok: boolean | null
}

const KINDS = [
  { value: 'postgresql', label: 'PostgreSQL' },
  { value: 'mysql', label: 'MySQL / MariaDB' },
  { value: 'bigquery', label: 'Google BigQuery' },
  { value: 'sqlite', label: 'SQLite (arquivo)' },
  { value: 'sqlserver', label: 'SQL Server' },
  { value: 'oracle', label: 'Oracle' },
  { value: 'firebird', label: 'Firebird' },
  { value: 'mongodb', label: 'MongoDB' },
]

const DEFAULT_PORTS: Record<string, number> = {
  mysql: 3306,
  postgresql: 5432,
  sqlserver: 1433,
  oracle: 1521,
  firebird: 3050,
  mongodb: 27017,
}

export default function Datasources() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const isMaster = user?.is_master ?? false

  const { data: sources = [], isLoading, error: loadError } = useQuery({
    queryKey: ['datasources'],
    queryFn: () => api<Datasource[]>('/datasources'),
  })
  const { data: tenants = [] } = useQuery({
    queryKey: ['tenants'],
    queryFn: listTenants,
    enabled: isMaster,
  })

  const [form, setForm] = useState({
    name: '',
    kind: 'postgresql',
    host: '',
    port: '',
    database: '',
    user: '',
    project: '',
    dataset: '',
    path: '',
    secret: '',
    tenant_id: '',
  })
  const [error, setError] = useState('')
  const [testResult, setTestResult] = useState<Record<string, string>>({})

  function buildConfig(): Record<string, unknown> {
    if (form.kind === 'bigquery') return { project: form.project, dataset: form.dataset }
    if (form.kind === 'sqlite') return { path: form.path }
    return {
      host: form.host,
      port: Number(form.port) || DEFAULT_PORTS[form.kind] || 5432,
      database: form.database,
      user: form.user,
    }
  }

  const create = useMutation({
    mutationFn: () =>
      api<Datasource>('/datasources', {
        method: 'POST',
        body: JSON.stringify({
          name: form.name,
          kind: form.kind,
          config: buildConfig(),
          secret: form.secret || null,
          tenant_id: isMaster ? form.tenant_id || null : null,
        }),
      }),
    onSuccess: () => {
      setForm({ ...form, name: '', secret: '' })
      setError('')
      qc.invalidateQueries({ queryKey: ['datasources'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  const test = useMutation({
    mutationFn: (id: string) =>
      api<{ ok: boolean; detail: string }>(`/datasources/${id}/test`, { method: 'POST' }),
    onSuccess: (result, id) => {
      setTestResult((t) => ({
        ...t,
        [id]: result.ok ? 'Conexão OK' : `Falhou: ${result.detail.slice(0, 120)}`,
      }))
      qc.invalidateQueries({ queryKey: ['datasources'] })
    },
  })

  const toggle = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      api<Datasource>(`/datasources/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ is_active }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['datasources'] }),
  })

  const archive = useMutation({
    mutationFn: (id: string) => api(`/datasources/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['datasources'] }),
  })

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    create.mutate()
  }

  // Nome histórico: na prática é "usa host/porta/database/user" — MongoDB
  // entra aqui também (mesma forma de conexão), mesmo não sendo SQL (a
  // consulta em si vai por query_mongo, tool própria, não run_sql_query).
  const isSql = ['postgresql', 'mysql', 'sqlserver', 'oracle', 'firebird', 'mongodb'].includes(
    form.kind
  )

  const activeCount = sources.filter((source) => source.is_active).length
  const testedCount = sources.filter((source) => source.last_test_ok).length

  return (
    <div className="space-y-6">
      <PageHeader
        title="Fontes de dados"
        description="Conecte bancos e datasets com contexto claro de ownership, credenciais e saúde operacional antes de expor acesso aos agentes."
        actions={
          <Button type="submit" form="datasource-form" disabled={create.isPending}>
            {create.isPending ? 'Salvando…' : 'Nova fonte'}
          </Button>
        }
      />

      <div className="grid gap-4 md:grid-cols-3">
        <StatCard label="Fontes totais" value={String(sources.length)} meta="Catálogo de bancos e datasets conectados." />
        <StatCard label="Fontes ativas" value={String(activeCount)} meta="Disponíveis para templates e fluxos em produção." />
        <StatCard label="Últimos testes OK" value={String(testedCount)} meta="Conexões com saúde confirmada recentemente." />
      </div>

      <Card title="Cadastrar fonte">
        <div className="space-y-6">
          <SectionIntro
            eyebrow="Conectividade"
            title="Defina engine, escopo e credenciais mínimas"
            description="Cadastre aliases claros para o runtime dos agentes e teste a conexão antes de expor a fonte em templates com leitura ou escrita controlada."
          />
          <form id="datasource-form" onSubmit={onSubmit} className="grid gap-4 lg:grid-cols-2">
            <Input
              label="Nome do alias"
              hint="Nome usado pelos agentes e pelo time operacional."
              required
              name="ds-name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
            <Select
              label="Tipo da fonte"
              hint="Escolha a engine compatível com o ambiente de destino."
              name="ds-kind"
              value={form.kind}
              onChange={(e) => setForm({ ...form, kind: e.target.value })}
            >
              {KINDS.map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label}
                </option>
              ))}
            </Select>
            {isSql && (
              <>
                <Input label="Host" hint="Hostname acessível pelo backend/kernel." required value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} />
                <Input label="Porta" hint="Use a porta padrão ou a exposta pelo ambiente." type="number" placeholder={String(DEFAULT_PORTS[form.kind] ?? 5432)} value={form.port} onChange={(e) => setForm({ ...form, port: e.target.value })} />
                <Input label="Database" hint="Banco ou schema primário da conexão." required value={form.database} onChange={(e) => setForm({ ...form, database: e.target.value })} />
                <Input label="Usuário" hint="Usuário técnico com o menor privilégio necessário." required value={form.user} onChange={(e) => setForm({ ...form, user: e.target.value })} />
                <Input label="Senha" hint="Gravada como segredo e nunca retornada em listagens." type="password" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} className="lg:col-span-2" />
              </>
            )}
            {form.kind === 'bigquery' && (
              <>
                <Input label="Projeto GCP" hint="Projeto que contém o dataset consultado." required value={form.project} onChange={(e) => setForm({ ...form, project: e.target.value })} />
                <Input label="Dataset" hint="Opcional; pode ser deixado em branco para uso mais flexível." value={form.dataset} onChange={(e) => setForm({ ...form, dataset: e.target.value })} />
                <Textarea
                  label="Service Account JSON"
                  hint="Cole a credencial completa em JSON para autenticação do BigQuery."
                  rows={5}
                  value={form.secret}
                  onChange={(e) => setForm({ ...form, secret: e.target.value })}
                  className="lg:col-span-2"
                  placeholder='{"type": "service_account", ...}'
                />
              </>
            )}
            {form.kind === 'sqlite' && (
              <Input label="Caminho do arquivo" hint="Use um path acessível pelo processo do runtime." required value={form.path} onChange={(e) => setForm({ ...form, path: e.target.value })} />
            )}
            {isMaster && (
              <Select
                label="Empresa"
                hint="Tenant proprietário desta conexão."
                required
                name="ds-tenant"
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

      <Card title="Inventário de fontes" actions={<Badge ok={activeCount > 0}>{activeCount} ativas</Badge>}>
        {isLoading ? (
          <TableSkeleton columns={5} />
        ) : loadError ? (
          <ErrorText>Não foi possível carregar os dados. Recarregue a página ou tente novamente.</ErrorText>
        ) : sources.length === 0 ? (
          <EmptyState
            title="Nenhuma fonte de dados cadastrada"
            description="Cadastre uma fonte acima para que os agentes possam consultar dados reais via SQL."
          />
        ) : (
          <Table headers={['Fonte', 'Tipo', 'Teste', 'Status', 'Ações']}>
            {sources.map((s) => (
              <tr key={s.id} data-testid="datasource-row" className="transition hover:bg-[var(--brand-soft)]/40">
                <td className="px-4 py-4 text-[var(--text)]">
                  <div>
                    <p className="font-medium">{s.name}</p>
                    <p className="mt-1 text-sm text-[var(--text-muted)]">{s.has_secret ? 'Segredo configurado para autenticação.' : 'Sem segredo salvo.'}</p>
                  </div>
                </td>
                <td className="px-4 py-4 text-[var(--text-muted)]">{s.kind}</td>
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
                  <Badge ok={s.is_active}>{s.is_active ? 'Ativa' : 'Inativa'}</Badge>
                </td>
                <td className="px-4 py-4 text-right">
                  <div className="flex justify-end gap-2">
                    <Button variant="ghost" onClick={() => toggle.mutate({ id: s.id, is_active: !s.is_active })}>
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
