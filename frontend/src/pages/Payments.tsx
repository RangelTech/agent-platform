import { useEffect, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorText,
  Input,
  PageHeader,
  Select,
  Table,
  TableSkeleton,
} from '../components/ui'
import { api, ApiError } from '../lib/api'

interface PaymentCredential {
  id: string
  provider: string
  has_token: boolean
  has_webhook_secret: boolean
  sandbox: boolean
  is_active: boolean
  webhook_path: string
  updated_at: string
}

interface PaymentCharge {
  id: string
  external_id: string
  amount: number
  description: string
  reference_id: string
  status: string
  sandbox: boolean
  ticket_url: string | null
  created_at: string
}

const STATUS_LABEL: Record<string, string> = {
  pending: 'Aguardando',
  paid: 'Pago',
  failed: 'Recusado',
  cancelled: 'Cancelado',
  refunded: 'Estornado',
}

export default function Payments() {
  const qc = useQueryClient()
  const {
    data: credentials = [],
    isLoading,
    error: loadError,
  } = useQuery({
    queryKey: ['payment-credentials'],
    queryFn: () => api<PaymentCredential[]>('/payments/credentials'),
  })
  const {
    data: charges = [],
    isLoading: chargesLoading,
    error: chargesError,
  } = useQuery({
    queryKey: ['payment-charges'],
    queryFn: () => api<PaymentCharge[]>('/payments/charges'),
  })

  const credential = credentials[0]
  const [accessToken, setAccessToken] = useState('')
  const [webhookSecret, setWebhookSecret] = useState('')
  const [sandbox, setSandbox] = useState('true')
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (credential) setSandbox(credential.sandbox ? 'true' : 'false')
  }, [credential])

  const save = useMutation({
    mutationFn: () =>
      api<PaymentCredential>('/payments/credentials', {
        method: 'PUT',
        body: JSON.stringify({
          // Campo em branco preserva o token já salvo — ele nunca volta do servidor.
          access_token: accessToken || undefined,
          webhook_secret: webhookSecret || undefined,
          sandbox: sandbox === 'true',
          is_active: true,
        }),
      }),
    onSuccess: () => {
      setAccessToken('')
      setWebhookSecret('')
      setSaved(true)
      qc.invalidateQueries({ queryKey: ['payment-credentials'] })
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Falha ao salvar'),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api(`/payments/credentials/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['payment-credentials'] }),
  })

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    setSaved(false)
    if (!credential && !accessToken) {
      setError('Informe o access token do Mercado Pago')
      return
    }
    save.mutate()
  }

  const webhookUrl = credential ? `${window.location.origin}${credential.webhook_path}` : ''

  return (
    <div className="space-y-6">
      <PageHeader
        title="Pagamentos"
        description="Credencial do Mercado Pago usada pelos agentes para gerar cobranças PIX. O token é gravado criptografado e nunca é exibido de volta."
        actions={credential ? <Badge ok={credential.has_token}>{credential.sandbox ? 'Sandbox' : 'Produção'}</Badge> : undefined}
      />

      <Card title="Credencial Mercado Pago">
        {isLoading ? (
          <TableSkeleton columns={3} rows={2} />
        ) : loadError ? (
          <ErrorText>Não foi possível carregar a credencial. Recarregue a página.</ErrorText>
        ) : (
          <form onSubmit={onSubmit} className="grid gap-4 lg:grid-cols-2">
            <Input
              label="Access token"
              hint={
                credential?.has_token
                  ? 'Já existe um token salvo. Preencha apenas para substituí-lo.'
                  : 'Access token da sua conta Mercado Pago (produção ou sandbox).'
              }
              type="password"
              name="payment-access-token"
              autoComplete="off"
              value={accessToken}
              onChange={(e) => setAccessToken(e.target.value)}
            />
            <Input
              label="Segredo do webhook"
              hint="Opcional, mas recomendado: valida a assinatura x-signature das notificações."
              type="password"
              name="payment-webhook-secret"
              autoComplete="off"
              value={webhookSecret}
              onChange={(e) => setWebhookSecret(e.target.value)}
            />
            <Select
              label="Ambiente"
              hint="Sandbox troca apenas a conta usada — o valor cobrado é sempre o valor real do pedido."
              name="payment-sandbox"
              value={sandbox}
              onChange={(e) => setSandbox(e.target.value)}
            >
              <option value="true">Sandbox (testes)</option>
              <option value="false">Produção</option>
            </Select>
            <div className="flex items-end gap-3">
              <Button type="submit" disabled={save.isPending}>
                {save.isPending ? 'Salvando…' : 'Salvar credencial'}
              </Button>
              {credential && (
                <Button type="button" variant="danger" onClick={() => remove.mutate(credential.id)}>
                  Remover
                </Button>
              )}
              {saved && <span className="pb-3 text-sm text-[var(--success)]">Salvo ✓</span>}
            </div>
            <div className="lg:col-span-2">
              <ErrorText>{error}</ErrorText>
            </div>
            {credential && (
              <div className="lg:col-span-2 rounded-2xl border border-[var(--border)] bg-[var(--surface-soft)] p-4">
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-faint)]">
                  URL de notificação
                </p>
                <p className="mt-2 break-all font-mono text-xs text-[var(--text-muted)]">{webhookUrl}</p>
                <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">
                  Cadastre esta URL como webhook de pagamentos no painel do Mercado Pago. Sem ela, o
                  agente ainda confirma o pagamento consultando o gateway sob demanda.
                </p>
              </div>
            )}
          </form>
        )}
      </Card>

      <Card title="Cobranças geradas">
        {chargesLoading ? (
          <TableSkeleton columns={5} />
        ) : chargesError ? (
          <ErrorText>Não foi possível carregar as cobranças.</ErrorText>
        ) : charges.length === 0 ? (
          <EmptyState
            title="Nenhuma cobrança gerada"
            description="Quando um agente usar a ferramenta de PIX, cada cobrança aparece aqui com o status atualizado."
          />
        ) : (
          <Table headers={['Cobrança', 'Referência', 'Valor', 'Status', 'Criada em']}>
            {charges.map((c) => (
              <tr key={c.id} data-testid="charge-row" className="transition hover:bg-[var(--brand-soft)]/40">
                <td className="px-4 py-4 font-mono text-xs text-[var(--text)]">{c.external_id}</td>
                <td className="px-4 py-4 text-[var(--text-muted)]">{c.reference_id || '—'}</td>
                <td className="px-4 py-4 text-[var(--text)]">
                  {c.amount.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}
                </td>
                <td className="px-4 py-4">
                  <Badge ok={c.status === 'paid'}>{STATUS_LABEL[c.status] ?? c.status}</Badge>
                </td>
                <td className="px-4 py-4 text-[var(--text-muted)]">
                  {new Date(c.created_at).toLocaleString('pt-BR')}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  )
}
