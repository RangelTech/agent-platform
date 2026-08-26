import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, EmptyState, ErrorText, Input, Table, TableSkeleton } from '../ui'
import { api, ApiError } from '../../lib/api'

interface GoogleAccount {
  id: string
  label: string
  email_address: string | null
  updated_at: string
}

interface InicioOAuth {
  auth_url: string
  redirect_uri: string
  state: string
}

/** Painel de "Contas Google" dentro da tela Credenciais (produto-08 §10).
 * Diferença real do que era a página `/contas-google`: campo de nome
 * editável antes de conectar (§9, ponto 3) -- sem isso, contas múltiplas
 * (ex. 1 agenda por médico de uma clínica) ficavam todas rotuladas só pelo
 * email do OAuth, sem jeito de escolher um nome de negócio. */
export function GoogleAccountsPanel() {
  const qc = useQueryClient()
  const {
    data: accounts = [],
    isLoading,
    error: loadError,
  } = useQuery({
    queryKey: ['google-accounts'],
    queryFn: () => api<GoogleAccount[]>('/google-accounts'),
  })

  const [nomeParaConectar, setNomeParaConectar] = useState('')
  const [inicio, setInicio] = useState<InicioOAuth | null>(null)
  const [error, setError] = useState('')

  const iniciar = useMutation({
    mutationFn: () => api<InicioOAuth>('/google-accounts/oauth/iniciar', { method: 'POST' }),
    onSuccess: (dados) => {
      setInicio(dados)
      window.open(dados.auth_url, '_blank', 'noopener')
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Falha ao iniciar autorização'),
  })

  const concluir = useMutation({
    mutationFn: (code: string) =>
      api('/google-accounts/oauth/concluir', {
        method: 'POST',
        body: JSON.stringify({ code, redirect_uri: inicio?.redirect_uri, label: nomeParaConectar || null }),
      }),
    onSuccess: () => {
      setInicio(null)
      setNomeParaConectar('')
      qc.invalidateQueries({ queryKey: ['google-accounts'] })
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Falha ao concluir conexão'),
  })

  // Mesma aba de callback do produto-08 (/oauth/callback) repassa o código
  // via postMessage — ver ContasIA.tsx, mesmo desenho.
  useEffect(() => {
    function aoReceberMensagem(evento: MessageEvent) {
      if (evento.origin !== window.location.origin) return
      const corpo = evento.data as { type?: string; dados?: { code?: string; error?: string } }
      if (corpo?.type !== 'oauth_callback' || !corpo.dados) return
      if (corpo.dados.error) {
        setError(corpo.dados.error)
        return
      }
      if (corpo.dados.code && inicio && !concluir.isPending) concluir.mutate(corpo.dados.code)
    }
    window.addEventListener('message', aoReceberMensagem)
    return () => window.removeEventListener('message', aoReceberMensagem)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inicio])

  const remove = useMutation({
    mutationFn: (id: string) => api(`/google-accounts/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['google-accounts'] }),
  })

  return (
    <div className="space-y-6">
      <Card title="Conectar conta Google">
        <div className="space-y-3">
          <Input
            label="Nome (opcional)"
            hint='Como esta conta aparece pros agentes quando há mais de uma (ex.: "Dr. Fulano"). Sem preencher, usa o email da conta.'
            value={nomeParaConectar}
            onChange={(e) => setNomeParaConectar(e.target.value)}
            disabled={iniciar.isPending || !!inicio}
          />
          <Button type="button" onClick={() => iniciar.mutate()} disabled={iniciar.isPending || !!inicio}>
            {iniciar.isPending ? 'Abrindo…' : 'Conectar com Google'}
          </Button>
          {concluir.isPending && (
            <p className="text-sm text-[var(--text-muted)]">Concluindo conexão…</p>
          )}
          <ErrorText>{error}</ErrorText>
        </div>
      </Card>

      <Card title="Contas Google conectadas">
        {isLoading ? (
          <TableSkeleton columns={3} rows={2} />
        ) : loadError ? (
          <ErrorText>Não foi possível carregar as contas. Recarregue a página.</ErrorText>
        ) : accounts.length === 0 ? (
          <EmptyState
            title="Nenhuma conta Google conectada"
            description="Conecte acima para os agentes conseguirem usar Calendar e Sheets. Conectando mais de uma, use o campo Nome pra distinguir (ex.: uma agenda por profissional)."
          />
        ) : (
          <Table headers={['Nome', 'Email', 'Conectada em', '']}>
            {accounts.map((a) => (
              <tr key={a.id} className="transition hover:bg-[var(--brand-soft)]/40">
                <td className="px-4 py-4 text-[var(--text)]">{a.label}</td>
                <td className="px-4 py-4 text-[var(--text-muted)]">{a.email_address || '—'}</td>
                <td className="px-4 py-4 text-[var(--text-muted)]">
                  {new Date(a.updated_at).toLocaleString('pt-BR')}
                </td>
                <td className="px-4 py-4 text-right">
                  <Button type="button" variant="danger" onClick={() => remove.mutate(a.id)}>
                    Desconectar
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
