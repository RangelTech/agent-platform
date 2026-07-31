import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge, Button, Card, EmptyState, ErrorText, PageHeader, Skeleton } from '../components/ui'
import { api, ApiError } from '../lib/api'

interface OmnichannelStatus {
  configured: boolean
  chatwoot_account_id: number | null
}

export default function Omnichannel() {
  const qc = useQueryClient()
  const [error, setError] = useState('')

  const {
    data: status,
    isLoading,
    error: loadError,
  } = useQuery({
    queryKey: ['omnichannel-status'],
    queryFn: () => api<OmnichannelStatus>('/omnichannel/status'),
  })

  const provision = useMutation({
    mutationFn: () => api<OmnichannelStatus>('/omnichannel/provision', { method: 'POST', body: '{}' }),
    onSuccess: () => {
      setError('')
      qc.invalidateQueries({ queryKey: ['omnichannel-status'] })
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Falha ao provisionar'),
  })

  const open = useMutation({
    mutationFn: () => api<{ url: string }>('/omnichannel/sso'),
    onSuccess: (body) => {
      setError('')
      // Link de uso único: abrir em outra aba mantém a plataforma aberta.
      if (body.url) window.open(body.url, '_blank', 'noopener')
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Falha ao abrir atendimento'),
  })

  return (
    <div className="space-y-6">
      <PageHeader
        title="Atendimento omnichannel"
        description="A operação de atendimento humano (WhatsApp, Instagram, Facebook) acontece no Chatwoot. O acesso é feito com a sua sessão daqui — sem novo cadastro e sem senha nova."
        actions={
          status ? (
            <Badge ok={Boolean(status.chatwoot_account_id)}>
              {status.chatwoot_account_id ? 'Provisionado' : 'Não provisionado'}
            </Badge>
          ) : undefined
        }
      />

      <Card title="Operação da empresa">
        {isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-6 w-64" />
            <Skeleton className="h-11 w-48" />
          </div>
        ) : loadError ? (
          <ErrorText>Não foi possível consultar o status da camada omnichannel.</ErrorText>
        ) : !status?.configured ? (
          <EmptyState
            title="Camada omnichannel não configurada"
            description="Esta instalação ainda não aponta para a ponte do Chatwoot. Configure BRIDGE_URL e BRIDGE_ADMIN_TOKEN no backend para habilitar."
          />
        ) : (
          <div className="space-y-4">
            <p className="text-sm leading-6 text-[var(--text-muted)]">
              {status.chatwoot_account_id
                ? 'Sua empresa já tem uma operação de atendimento criada. Abra o painel para responder conversas, organizar filas e acompanhar o time.'
                : 'Crie a operação de atendimento da sua empresa. Isso provisiona a conta no Chatwoot e espelha o seu usuário como administrador dela.'}
            </p>
            <div className="flex flex-wrap items-center gap-3">
              {status.chatwoot_account_id ? (
                <Button onClick={() => open.mutate()} disabled={open.isPending}>
                  {open.isPending ? 'Abrindo…' : 'Abrir atendimento'}
                </Button>
              ) : (
                <Button onClick={() => provision.mutate()} disabled={provision.isPending}>
                  {provision.isPending ? 'Provisionando…' : 'Criar operação de atendimento'}
                </Button>
              )}
            </div>
            <ErrorText>{error}</ErrorText>
          </div>
        )}
      </Card>
    </div>
  )
}
