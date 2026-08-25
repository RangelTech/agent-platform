import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, EmptyState, ErrorText, PageHeader, Table, TableSkeleton } from '../components/ui'
import { api, ApiError } from '../lib/api'

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

export default function GoogleAccounts() {
  const qc = useQueryClient()
  const {
    data: accounts = [],
    isLoading,
    error: loadError,
  } = useQuery({
    queryKey: ['google-accounts'],
    queryFn: () => api<GoogleAccount[]>('/google-accounts'),
  })

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
        body: JSON.stringify({ code, redirect_uri: inicio?.redirect_uri }),
      }),
    onSuccess: () => {
      setInicio(null)
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
      <PageHeader
        title="Contas Google"
        description="Conta Google usada pelos agentes para consultar/marcar compromissos (Calendar) e ler/editar planilhas (Sheets)."
      />

      <Card title="Conectar conta Google">
        <div className="space-y-3">
          <Button type="button" onClick={() => iniciar.mutate()} disabled={iniciar.isPending}>
            {iniciar.isPending ? 'Abrindo…' : 'Conectar com Google'}
          </Button>
          {concluir.isPending && (
            <p className="text-sm text-[var(--text-muted)]">Concluindo conexão…</p>
          )}
          <ErrorText>{error}</ErrorText>
        </div>
      </Card>

      <Card title="Contas conectadas">
        {isLoading ? (
          <TableSkeleton columns={3} rows={2} />
        ) : loadError ? (
          <ErrorText>Não foi possível carregar as contas. Recarregue a página.</ErrorText>
        ) : accounts.length === 0 ? (
          <EmptyState
            title="Nenhuma conta Google conectada"
            description="Clique em Conectar com Google acima para os agentes conseguirem usar Calendar e Sheets."
          />
        ) : (
          <Table headers={['Conta', 'Conectada em', '']}>
            {accounts.map((a) => (
              <tr key={a.id} className="transition hover:bg-[var(--brand-soft)]/40">
                <td className="px-4 py-4 text-[var(--text)]">{a.email_address || a.label}</td>
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
