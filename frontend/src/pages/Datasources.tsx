import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Badge, Button, Card, ErrorText, Input, Select, Table } from '../components/ui'
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
]

export default function Datasources() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const isMaster = user?.is_master ?? false

  const { data: sources = [], isLoading } = useQuery({
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
      port: Number(form.port) || (form.kind === 'mysql' ? 3306 : 5432),
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

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    create.mutate()
  }

  const isSql = form.kind === 'postgresql' || form.kind === 'mysql'

  return (
    <div className="space-y-6">
      <Card title="Nova fonte de dados">
        <form onSubmit={onSubmit} className="grid gap-3 sm:grid-cols-2">
          <Input
            label="Nome (alias usado pelos agentes)"
            required
            name="ds-name"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <Select
            label="Tipo"
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
              <Input label="Host" required value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} />
              <Input label="Porta" type="number" placeholder={form.kind === 'mysql' ? '3306' : '5432'} value={form.port} onChange={(e) => setForm({ ...form, port: e.target.value })} />
              <Input label="Database" required value={form.database} onChange={(e) => setForm({ ...form, database: e.target.value })} />
              <Input label="Usuário" required value={form.user} onChange={(e) => setForm({ ...form, user: e.target.value })} />
              <Input label="Senha" type="password" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} />
            </>
          )}
          {form.kind === 'bigquery' && (
            <>
              <Input label="Projeto GCP" required value={form.project} onChange={(e) => setForm({ ...form, project: e.target.value })} />
              <Input label="Dataset (opcional)" value={form.dataset} onChange={(e) => setForm({ ...form, dataset: e.target.value })} />
              <label className="block sm:col-span-2">
                <span className="mb-1 block text-sm text-slate-300">Service Account JSON</span>
                <textarea
                  rows={3}
                  value={form.secret}
                  onChange={(e) => setForm({ ...form, secret: e.target.value })}
                  className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100 outline-none focus:border-indigo-500"
                  placeholder='{"type": "service_account", ...}'
                />
              </label>
            </>
          )}
          {form.kind === 'sqlite' && (
            <Input label="Caminho do arquivo" required value={form.path} onChange={(e) => setForm({ ...form, path: e.target.value })} />
          )}
          {isMaster && (
            <Select
              label="Empresa"
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
          <div className="sm:col-span-2 flex items-center gap-3">
            <Button type="submit" disabled={create.isPending}>
              Cadastrar fonte
            </Button>
            <ErrorText>{error}</ErrorText>
          </div>
        </form>
      </Card>

      <Card title="Fontes de dados">
        {isLoading ? (
          <p className="text-sm text-slate-400">Carregando…</p>
        ) : (
          <Table headers={['Nome', 'Tipo', 'Teste', 'Status', '']}>
            {sources.map((s) => (
              <tr key={s.id} data-testid="datasource-row">
                <td className="px-3 py-2 text-slate-200">{s.name}</td>
                <td className="px-3 py-2 text-slate-400">{s.kind}</td>
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
                  <Badge ok={s.is_active}>{s.is_active ? 'Ativa' : 'Inativa'}</Badge>
                </td>
                <td className="px-3 py-2 text-right">
                  <Button variant="ghost" onClick={() => toggle.mutate({ id: s.id, is_active: !s.is_active })}>
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
