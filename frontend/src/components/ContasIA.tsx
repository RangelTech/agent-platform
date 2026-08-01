import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge, Button, Card, EmptyState, ErrorText, Input, Select, Skeleton } from './ui'
import { api, ApiError } from '../lib/api'

/**
 * Contas de IA da empresa e os combos que revezam entre elas.
 *
 * O cliente escolhe conta e combo; a plataforma cuida do resto. Cada empresa
 * tem uma instância de roteamento própria — é isso que garante que a conta de
 * um cliente nunca atenda a chamada de outro.
 */

interface StatusRouter {
  provisionado: boolean
  contas?: number
  combos?: number
}

interface Conta {
  id: string
  provider: string
  auth_type: string
  label: string
  conectada: boolean
}

interface Modelo {
  id: string
  provider: string | null
}

interface Combo {
  id: string
  name: string
  models: string[]
  ai_service_id: string | null
}

const PROVEDORES = [
  { value: 'gemini', label: 'Google Gemini' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic Claude' },
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'groq', label: 'Groq' },
]

export function ContasIA() {
  const qc = useQueryClient()
  const [erro, setErro] = useState('')

  const { data: status, isLoading } = useQuery({
    queryKey: ['ai-router-status'],
    queryFn: () => api<StatusRouter>('/ai-router/status'),
  })
  const provisionado = status?.provisionado ?? false

  const { data: contas = [] } = useQuery({
    queryKey: ['ai-router-contas'],
    queryFn: () => api<Conta[]>('/ai-router/contas'),
    enabled: provisionado,
  })
  const { data: modelos = [] } = useQuery({
    queryKey: ['ai-router-modelos'],
    queryFn: () => api<Modelo[]>('/ai-router/modelos'),
    enabled: provisionado,
  })
  const { data: combos = [] } = useQuery({
    queryKey: ['ai-router-combos'],
    queryFn: () => api<Combo[]>('/ai-router/combos'),
    enabled: provisionado,
  })

  const [conta, setConta] = useState({ provider: 'gemini', api_key: '', label: '' })
  const [comboNome, setComboNome] = useState('')
  const [comboModelos, setComboModelos] = useState<string[]>([])

  const criarConta = useMutation({
    mutationFn: () => api('/ai-router/contas', { method: 'POST', body: JSON.stringify(conta) }),
    onSuccess: () => {
      setConta({ provider: 'gemini', api_key: '', label: '' })
      setErro('')
      qc.invalidateQueries({ queryKey: ['ai-router-contas'] })
      qc.invalidateQueries({ queryKey: ['ai-router-modelos'] })
    },
    onError: (e) => setErro(e instanceof ApiError ? e.message : 'Falha ao conectar conta'),
  })

  const sincronizar = useMutation({
    mutationFn: () => api<{ novas: number }>('/ai-router/sincronizar', { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ai-router-contas'] })
      qc.invalidateQueries({ queryKey: ['ai-router-modelos'] })
    },
    onError: (e) => setErro(e instanceof ApiError ? e.message : 'Falha ao sincronizar'),
  })

  const removerConta = useMutation({
    mutationFn: (id: string) => api(`/ai-router/contas/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ai-router-contas'] }),
  })

  const criarCombo = useMutation({
    mutationFn: () =>
      api('/ai-router/combos', {
        method: 'POST',
        body: JSON.stringify({ name: comboNome, models: comboModelos }),
      }),
    onSuccess: () => {
      setComboNome('')
      setComboModelos([])
      setErro('')
      qc.invalidateQueries({ queryKey: ['ai-router-combos'] })
      qc.invalidateQueries({ queryKey: ['ai-services'] })
    },
    onError: (e) => setErro(e instanceof ApiError ? e.message : 'Falha ao criar combo'),
  })

  const removerCombo = useMutation({
    mutationFn: (id: string) => api(`/ai-router/combos/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ai-router-combos'] })
      qc.invalidateQueries({ queryKey: ['ai-services'] })
    },
  })

  if (isLoading) return <Skeleton className="h-40" />

  if (!provisionado) {
    return (
      <Card title="Contas de IA da empresa">
        <EmptyState
          title="Instância de IA ainda não provisionada"
          description="Cada empresa atende com as próprias contas, numa instância dedicada. Peça ao administrador da plataforma para provisionar a desta empresa."
        />
      </Card>
    )
  }

  return (
    <>
      <Card
        title="Contas de IA da empresa"
        actions={
          <Button variant="ghost" onClick={() => sincronizar.mutate()} disabled={sincronizar.isPending}>
            {sincronizar.isPending ? 'Sincronizando…' : 'Sincronizar contas'}
          </Button>
        }
      >
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault()
            setErro('')
            criarConta.mutate()
          }}
          className="grid gap-4 lg:grid-cols-4"
        >
          <Select
            label="Provedor"
            name="conta-provider"
            value={conta.provider}
            onChange={(e) => setConta({ ...conta, provider: e.target.value })}
          >
            {PROVEDORES.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </Select>
          <Input
            label="Apelido"
            hint="Como esta conta aparece para o seu time."
            name="conta-label"
            value={conta.label}
            onChange={(e) => setConta({ ...conta, label: e.target.value })}
          />
          <Input
            label="Chave da conta"
            hint="Fica guardada cifrada e nunca é exibida de volta."
            type="password"
            name="conta-key"
            autoComplete="off"
            value={conta.api_key}
            onChange={(e) => setConta({ ...conta, api_key: e.target.value })}
          />
          <div className="flex items-end">
            <Button type="submit" disabled={criarConta.isPending || !conta.api_key || !conta.label}>
              {criarConta.isPending ? 'Conectando…' : 'Conectar conta'}
            </Button>
          </div>
          <p className="lg:col-span-4 text-sm leading-6 text-[var(--text-muted)]">
            Contas de assinatura (Claude, Codex) são conectadas por login na instância da empresa e
            entram aqui com <strong>Sincronizar contas</strong>.
          </p>
          <div className="lg:col-span-4">
            <ErrorText>{erro}</ErrorText>
          </div>
        </form>

        <div className="mt-6 space-y-2">
          {contas.length === 0 ? (
            <EmptyState
              title="Nenhuma conta conectada"
              description="Conecte a primeira conta acima para que os agentes desta empresa possam responder."
            />
          ) : (
            contas.map((c) => (
              <div
                key={c.id}
                data-testid="conta-ia"
                className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-3"
              >
                <div>
                  <p className="text-sm font-medium text-[var(--text)]">{c.label}</p>
                  <p className="text-xs text-[var(--text-muted)]">
                    {c.provider} · {c.auth_type === 'oauth' ? 'assinatura' : 'chave de API'}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <Badge ok={c.conectada}>{c.conectada ? 'Ativa' : 'Pendente'}</Badge>
                  <Button variant="danger" onClick={() => removerConta.mutate(c.id)}>
                    Remover
                  </Button>
                </div>
              </div>
            ))
          )}
        </div>
      </Card>

      <Card title="Combos">
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault()
            setErro('')
            criarCombo.mutate()
          }}
          className="grid gap-4 lg:grid-cols-2"
        >
          <Input
            label="Nome do combo"
            hint="É este nome que aparece ao escolher o serviço de IA de um template."
            name="combo-nome"
            value={comboNome}
            onChange={(e) => setComboNome(e.target.value)}
          />
          <div>
            <span className="block text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
              Modelos que revezam
            </span>
            <p className="mt-1 text-sm leading-5 text-[var(--text-faint)]">
              As chamadas alternam entre os modelos marcados, na ordem em que você marcar.
            </p>
            <div className="mt-2 max-h-40 space-y-1 overflow-y-auto rounded-2xl border border-[var(--border)] p-2">
              {modelos.length === 0 && (
                <p className="px-2 py-3 text-sm text-[var(--text-faint)]">
                  Conecte uma conta para ver os modelos disponíveis.
                </p>
              )}
              {modelos.map((m) => (
                <label key={m.id} className="flex items-center gap-2 px-2 py-1 text-sm text-[var(--text)]">
                  <input
                    type="checkbox"
                    checked={comboModelos.includes(m.id)}
                    onChange={(e) =>
                      setComboModelos(
                        e.target.checked
                          ? [...comboModelos, m.id]
                          : comboModelos.filter((x) => x !== m.id),
                      )
                    }
                  />
                  {m.id}
                </label>
              ))}
            </div>
          </div>
          <div className="lg:col-span-2">
            <Button
              type="submit"
              disabled={criarCombo.isPending || !comboNome || comboModelos.length === 0}
            >
              {criarCombo.isPending ? 'Criando…' : 'Criar combo'}
            </Button>
          </div>
        </form>

        <div className="mt-6 space-y-2">
          {combos.length === 0 ? (
            <EmptyState
              title="Nenhum combo criado"
              description="Um combo agrupa as contas da empresa e vira uma opção de serviço de IA nos templates."
            />
          ) : (
            combos.map((c) => (
              <div
                key={c.id}
                data-testid="combo-ia"
                className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-3"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-[var(--text)]">{c.name}</p>
                  <p className="truncate text-xs text-[var(--text-muted)]">{c.models.join(' · ')}</p>
                </div>
                <Button variant="danger" onClick={() => removerCombo.mutate(c.id)}>
                  Remover
                </Button>
              </div>
            ))
          )}
        </div>
      </Card>
    </>
  )
}
