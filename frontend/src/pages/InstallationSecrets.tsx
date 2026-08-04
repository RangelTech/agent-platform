import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorText,
  Input,
  PageHeader,
  Skeleton,
} from '../components/ui'
import { api, ApiError } from '../lib/api'

interface Sync {
  target: string
  status: string
  detail: string
  synced_version: number | null
}

interface Segredo {
  id: string
  name: string
  description: string
  targets: string[]
  version: number
  is_active: boolean
  updated_at: string
  sync: Sync[]
}

/** Chaves que a instalação costuma precisar, com o que cada uma liga. */
const SUGESTOES = [
  { name: 'FB_APP_ID', description: 'App da Meta — liga Instagram e Messenger', chatwoot: true },
  { name: 'FB_APP_SECRET', description: 'Segredo do app da Meta', chatwoot: true },
  { name: 'FB_VERIFY_TOKEN', description: 'Token de verificação do webhook do Messenger', chatwoot: true },
  { name: 'IG_VERIFY_TOKEN', description: 'Token de verificação do webhook do Instagram', chatwoot: true },
  { name: 'SERPER_API_KEY', description: 'Busca na web usada pelos agentes', chatwoot: false },
]

function estado(sync: Sync[]): { texto: string; ok: boolean } {
  if (!sync.length) return { texto: 'Só nesta plataforma', ok: true }
  const erro = sync.find((s) => s.status === 'error')
  if (erro) return { texto: `Erro em ${erro.target}: ${erro.detail}`, ok: false }
  if (sync.some((s) => s.status !== 'ok')) return { texto: 'Aguardando propagação', ok: false }
  return { texto: 'Propagado', ok: true }
}

export default function InstallationSecrets() {
  const qc = useQueryClient()
  const [error, setError] = useState('')
  const [nome, setNome] = useState('')
  const [valor, setValor] = useState('')
  const [descricao, setDescricao] = useState('')
  const [paraChatwoot, setParaChatwoot] = useState(false)

  const segredos = useQuery({
    queryKey: ['installation-secrets'],
    queryFn: () => api<Segredo[]>('/installation-secrets'),
  })

  const salvar = useMutation({
    mutationFn: () =>
      api('/installation-secrets', {
        method: 'POST',
        body: JSON.stringify({
          name: nome,
          value: valor,
          description: descricao,
          targets: paraChatwoot ? ['chatwoot'] : [],
        }),
      }),
    onSuccess: () => {
      setError('')
      setNome('')
      setValor('')
      setDescricao('')
      qc.invalidateQueries({ queryKey: ['installation-secrets'] })
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Falha ao salvar a chave'),
  })

  const remover = useMutation({
    mutationFn: (id: string) => api(`/installation-secrets/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['installation-secrets'] }),
  })

  function preencher(sugestao: (typeof SUGESTOES)[number]) {
    setNome(sugestao.name)
    setDescricao(sugestao.description)
    setParaChatwoot(sugestao.chatwoot)
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Chaves da instalação"
        description="Chaves que valem para a instalação inteira, não para uma empresa: app da Meta, busca na web, integrações. Ficam cifradas neste banco — não é preciso mexer em variável de ambiente nem refazer deploy para trocá-las."
      />

      <Card title="Cadastrar ou substituir">
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            {SUGESTOES.map((s) => (
              <Button key={s.name} type="button" variant="ghost" onClick={() => preencher(s)}>
                {s.name}
              </Button>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Input
              label="Nome"
              hint="Maiúsculas, números e _ (ex.: FB_APP_ID)"
              value={nome}
              onChange={(e) => setNome(e.target.value.toUpperCase())}
            />
            <Input
              label="Valor"
              hint="Guardado cifrado. Depois de salvo, ninguém lê de volta — nem você."
              type="password"
              value={valor}
              onChange={(e) => setValor(e.target.value)}
            />
          </div>
          <Input
            label="Para que serve"
            value={descricao}
            onChange={(e) => setDescricao(e.target.value)}
          />
          <label className="flex items-start gap-2 text-sm text-[var(--text-muted)]">
            <input
              type="checkbox"
              checked={paraChatwoot}
              onChange={(e) => setParaChatwoot(e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-[var(--brand)]"
            />
            <span>
              Enviar também para o atendimento (Chatwoot)
              <span className="mt-0.5 block text-xs">
                Necessário para as chaves da Meta: é o que liga os canais de Instagram e
                Messenger. A propagação acontece logo depois de salvar e o estado aparece
                na lista abaixo.
              </span>
            </span>
          </label>
          <div className="flex items-center gap-3">
            <Button
              onClick={() => salvar.mutate()}
              disabled={salvar.isPending || !nome || !valor}
            >
              {salvar.isPending ? 'Salvando…' : 'Salvar chave'}
            </Button>
            <ErrorText>{error}</ErrorText>
          </div>
        </div>
      </Card>

      <Card title="Chaves cadastradas">
        {segredos.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : segredos.error ? (
          <ErrorText>Não foi possível listar as chaves.</ErrorText>
        ) : !segredos.data?.length ? (
          <EmptyState
            title="Nenhuma chave cadastrada"
            description="Enquanto não houver chave aqui, a instalação continua usando o que estiver na variável de ambiente de cada serviço."
          />
        ) : (
          <div className="space-y-3">
            {segredos.data.map((s) => {
              const situacao = estado(s.sync)
              return (
                <div
                  key={s.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-[var(--border)] p-4"
                >
                  <div>
                    <div className="text-sm font-medium text-[var(--text)]">{s.name}</div>
                    <div className="text-xs text-[var(--text-faint)]">
                      {s.description || 'sem descrição'} · versão {s.version} ·{' '}
                      {new Date(s.updated_at).toLocaleString('pt-BR')}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <Badge ok={situacao.ok}>{situacao.texto}</Badge>
                    <Button
                      variant="ghost"
                      onClick={() => remover.mutate(s.id)}
                      disabled={remover.isPending}
                    >
                      Remover
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </Card>
    </div>
  )
}
