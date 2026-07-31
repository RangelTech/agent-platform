import { useQuery } from '@tanstack/react-query'
import { Card, EmptyState, ErrorText, PageHeader, Table, TableSkeleton } from '../components/ui'
import { api } from '../lib/api'

interface UsageSummary {
  totals: {
    calls: number
    prompt_tokens: number
    completion_tokens: number
    cost_usd: number
    avg_latency_ms: number
  }
  by_model: { provider: string; model: string; calls: number; tokens: number; cost_usd: number }[]
  by_day: { day: string; tokens: number; cost_usd: number }[]
}

interface FeedbackRow {
  rating: number
  comment: string
  message: string
  user: string
  created_at: string
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[20px] border border-[var(--border)] bg-[var(--surface)] px-4 py-3.5 shadow-[0_20px_60px_-32px_rgba(15,23,42,0.35)]">
      <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">{label}</p>
      <p className="mt-1 text-xl font-semibold text-[var(--text)]">{value}</p>
    </div>
  )
}

export default function Usage() {
  const { data, isLoading: usageLoading, error: usageError } = useQuery({
    queryKey: ['usage'],
    queryFn: () => api<UsageSummary>('/usage?days=30'),
  })
  const { data: feedback = [], isLoading: feedbackLoading, error: feedbackError } = useQuery({
    queryKey: ['feedback'],
    queryFn: () => api<FeedbackRow[]>('/feedback'),
  })

  const totals = data?.totals
  return (
    <div className="space-y-6">
      <PageHeader title="Consumo" description="Custos, tokens e avaliações dos últimos 30 dias." />

      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Chamadas (30d)" value={String(totals?.calls ?? '—')} />
        <Stat
          label="Tokens"
          value={totals ? `${((totals.prompt_tokens + totals.completion_tokens) / 1000).toFixed(1)}k` : '—'}
        />
        <Stat label="Custo estimado" value={totals ? `US$ ${totals.cost_usd.toFixed(4)}` : '—'} />
        <Stat label="Latência média" value={totals ? `${totals.avg_latency_ms} ms` : '—'} />
      </div>

      <Card title="Por modelo (30 dias)">
        {usageLoading ? (
          <TableSkeleton columns={5} />
        ) : usageError ? (
          <ErrorText>Não foi possível carregar o consumo. Recarregue a página ou tente novamente.</ErrorText>
        ) : (data?.by_model ?? []).length === 0 ? (
          <EmptyState
            title="Sem consumo registrado nos últimos 30 dias"
            description="Assim que houver conversas neste tenant, o custo por modelo aparece aqui."
          />
        ) : (
        <Table headers={['Provider', 'Modelo', 'Chamadas', 'Tokens', 'Custo (US$)']}>
          {(data?.by_model ?? []).map((r, i) => (
            <tr key={i} data-testid="usage-model-row" className="transition hover:bg-[var(--brand-soft)]">
              <td className="px-3 py-2 text-[var(--text-muted)]">{r.provider}</td>
              <td className="px-3 py-2 font-mono text-xs text-[var(--text)]">{r.model}</td>
              <td className="px-3 py-2 text-[var(--text-muted)]">{r.calls}</td>
              <td className="px-3 py-2 text-[var(--text-muted)]">{r.tokens.toLocaleString('pt-BR')}</td>
              <td className="px-3 py-2 text-[var(--text-muted)]">{r.cost_usd.toFixed(4)}</td>
            </tr>
          ))}
        </Table>
        )}
      </Card>

      <Card title="Avaliações dos usuários">
        {feedbackLoading ? (
          <TableSkeleton columns={5} />
        ) : feedbackError ? (
          <ErrorText>Não foi possível carregar as avaliações. Recarregue a página ou tente novamente.</ErrorText>
        ) : feedback.length === 0 ? (
          <EmptyState
            title="Nenhuma avaliação ainda"
            description="Os 👍/👎 dados pelos usuários no chat aparecem aqui com o comentário associado."
          />
        ) : (
          <Table headers={['', 'Comentário', 'Resposta avaliada', 'Usuário', 'Quando']}>
            {feedback.map((f, i) => (
              <tr key={i} data-testid="feedback-row" className="transition hover:bg-[var(--brand-soft)]">
                <td className="px-3 py-2">{f.rating > 0 ? '👍' : '👎'}</td>
                <td className="px-3 py-2 text-[var(--text)]">{f.comment || '—'}</td>
                <td className="px-3 py-2 text-[var(--text-faint)]">{f.message}</td>
                <td className="px-3 py-2 text-[var(--text-muted)]">{f.user}</td>
                <td className="px-3 py-2 text-[var(--text-faint)]">
                  {new Date(f.created_at).toLocaleString('pt-BR')}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  )
}
