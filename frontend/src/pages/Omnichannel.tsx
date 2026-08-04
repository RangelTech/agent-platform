import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorText,
  PageHeader,
  Select,
  Skeleton,
} from '../components/ui'
import { api, ApiError } from '../lib/api'

interface OmnichannelStatus {
  configured: boolean
  chatwoot_account_id: number | null
}

interface CaixaIa {
  template_id: string | null
  template_name: string
  autopilot: boolean
  has_integration_key: boolean
}

interface Caixa {
  chatwoot_inbox_id: number
  name: string
  channel_type: string
  ai: CaixaIa | null
  inherits_default: boolean
}

interface Caixas {
  default: CaixaIa | null
  inboxes: Caixa[]
}

/** O tipo do canal vem do Chatwoot como `Channel::FacebookPage`. */
function canal(tipo: string): string {
  const nome = tipo.split('::').pop() ?? tipo
  return (
    {
      Api: 'API (WhatsApp)',
      FacebookPage: 'Facebook / Instagram',
      WebWidget: 'Site',
      Email: 'E-mail',
      TwilioSms: 'SMS',
      Whatsapp: 'WhatsApp oficial',
    }[nome] ?? nome
  )
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

  const caixas = useQuery({
    queryKey: ['omnichannel-inboxes'],
    // Só faz sentido perguntar depois que a conta existe.
    enabled: Boolean(status?.chatwoot_account_id),
    queryFn: () => api<Caixas>('/omnichannel/inboxes'),
  })

  const templates = useQuery({
    queryKey: ['templates'],
    enabled: Boolean(status?.chatwoot_account_id),
    queryFn: () => api<{ id: string; name: string }[]>('/templates'),
  })

  const vincular = useMutation({
    mutationFn: ({ inbox, templateId }: { inbox: number; templateId: string }) =>
      api(`/omnichannel/inboxes/${inbox}/ia`, {
        method: 'PUT',
        body: JSON.stringify({
          template_id: templateId || null,
          // Sem template escolhido a IA não deve atender sozinha na caixa.
          autopilot: Boolean(templateId),
        }),
      }),
    onSuccess: () => {
      setError('')
      qc.invalidateQueries({ queryKey: ['omnichannel-inboxes'] })
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Falha ao vincular o agente'),
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

      {status?.chatwoot_account_id ? (
        <Card title="Quem responde em cada caixa">
          {caixas.isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : caixas.error ? (
            <ErrorText>Não foi possível listar as caixas de atendimento.</ErrorText>
          ) : !caixas.data?.inboxes.length ? (
            <EmptyState
              title="Nenhuma caixa de atendimento ainda"
              description="Conecte um canal (WhatsApp, Instagram, site) no painel de atendimento. Ele aparece aqui para você escolher qual agente responde."
            />
          ) : (
            <div className="space-y-4">
              <p className="text-sm leading-6 text-[var(--text-muted)]">
                Cada caixa pode ser atendida por um agente diferente da sua empresa — o
                do financeiro no WhatsApp da cobrança, o de vendas no Instagram. Sem
                agente escolhido, a caixa fica só para atendimento humano.
              </p>
              {caixas.data.inboxes.map((caixa) => (
                <div
                  key={caixa.chatwoot_inbox_id}
                  className="grid gap-3 rounded-2xl border border-[var(--border)] p-4 sm:grid-cols-[1fr_auto] sm:items-end"
                >
                  <div>
                    <div className="text-sm font-medium text-[var(--text)]">{caixa.name}</div>
                    <div className="text-xs text-[var(--text-faint)]">
                      {canal(caixa.channel_type)}
                      {caixa.inherits_default ? ' · usando o agente padrão da empresa' : ''}
                    </div>
                  </div>
                  <Select
                    label="Agente que responde"
                    value={caixa.ai?.template_id ?? ''}
                    disabled={vincular.isPending}
                    onChange={(e) =>
                      vincular.mutate({
                        inbox: caixa.chatwoot_inbox_id,
                        templateId: e.target.value,
                      })
                    }
                  >
                    <option value="">Somente atendimento humano</option>
                    {(templates.data ?? []).map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </Select>
                </div>
              ))}
              <ErrorText>{error}</ErrorText>
            </div>
          )}
        </Card>
      ) : null}
    </div>
  )
}
