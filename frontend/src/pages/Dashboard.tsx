import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { Link } from 'react-router-dom'
import { Card, EmptyState, ErrorText, Skeleton } from '../components/ui'
import { api } from '../lib/api'
import { type ChatSummary } from '../lib/chat'
import { fadeUp, staggerContainer } from '../lib/motion'
import { listTemplates, type TemplateSummary } from '../lib/templates'

interface UsageSummary {
  totals: {
    calls: number
    prompt_tokens: number
    completion_tokens: number
    cost_usd: number
    avg_latency_ms: number
  }
}

function formatDate(value: string | undefined) {
  if (!value) return 'Agora'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Agora'
  return new Intl.DateTimeFormat('pt-BR', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function StatTile({
  eyebrow,
  title,
  value,
  detail,
  loading,
}: {
  eyebrow: string
  title: string
  value: string
  detail: string
  loading?: boolean
}) {
  return (
    <motion.div
      variants={fadeUp}
      className="rounded-[24px] border border-[var(--border)] bg-[var(--surface-elevated)] p-5 shadow-[0_20px_60px_-40px_rgba(15,23,42,0.45)]"
    >
      <p className="text-[11px] font-medium uppercase tracking-[0.22em] text-[var(--text-faint)]">{eyebrow}</p>
      <p className="mt-4 text-sm font-medium text-[var(--text-muted)]">{title}</p>
      {loading ? (
        <Skeleton className="mt-3 h-8 w-24" />
      ) : (
        <p className="mt-2 text-3xl font-semibold tracking-tight text-[var(--text)]">{value}</p>
      )}
      <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">{detail}</p>
    </motion.div>
  )
}

export default function Dashboard() {
  const { data: chats = [], isLoading: chatsLoading, error: chatsError } = useQuery({
    queryKey: ['dashboard', 'chats'],
    queryFn: () => api<ChatSummary[]>('/chats'),
  })
  const { data: templates = [], isLoading: templatesLoading, error: templatesError } = useQuery({
    queryKey: ['dashboard', 'templates'],
    queryFn: listTemplates,
  })
  const { data: usage, isLoading: usageLoading } = useQuery({
    queryKey: ['dashboard', 'usage'],
    queryFn: () => api<UsageSummary>('/usage?days=30'),
  })

  const activeTemplates = useMemo(
    () => templates.filter((template) => template.active_version_id),
    [templates],
  )
  const recentChats = useMemo(() => chats.slice(0, 5), [chats])
  const featuredTemplates = useMemo(() => activeTemplates.slice(0, 4), [activeTemplates])
  const totalTokens = (usage?.totals.prompt_tokens ?? 0) + (usage?.totals.completion_tokens ?? 0)

  return (
    <div className="space-y-8">
      <section className="overflow-hidden rounded-[32px] border border-[var(--border)] bg-[image:var(--shell-gradient)] p-6 shadow-[0_30px_120px_-60px_rgba(15,23,42,0.5)] sm:p-8">
        <div className="grid gap-8 xl:grid-cols-[minmax(0,1.15fr)_360px] xl:items-end">
          <div>
            <span className="inline-flex rounded-full border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-1 text-[11px] font-medium uppercase tracking-[0.2em] text-[var(--text-muted)]">
              Live workspace
            </span>
            <h1 className="mt-5 max-w-3xl text-3xl font-semibold tracking-tight text-[var(--text)] sm:text-4xl">
              Painel para acompanhar conversas, consumo e templates ativos em tempo real.
            </h1>
            <p className="mt-4 max-w-2xl text-base leading-7 text-[var(--text-muted)]">
              Uma visão inicial mais forte para a operação: métricas de uso, últimas conversas e atalhos para os fluxos
              que mais importam.
            </p>
          </div>

          <div className="grid gap-4">
            <div className="rounded-[24px] border border-[var(--border)] bg-[var(--surface-elevated)] p-5">
              <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-faint)]">Atividade recente</p>
              <p className="mt-3 text-lg font-semibold text-[var(--text)]">{chats.length} conversas disponíveis</p>
              <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">
                {recentChats.length > 0
                  ? `Última atualização visível em ${formatDate(recentChats[0]?.updated_at)}.`
                  : 'Nenhuma conversa registrada até o momento.'}
              </p>
            </div>
            <div className="rounded-[24px] border border-[var(--border)] bg-[var(--surface-elevated)] p-5">
              <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-faint)]">Catálogo ativo</p>
              <p className="mt-3 text-lg font-semibold text-[var(--text)]">
                {activeTemplates.length} templates publicados
              </p>
              <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">
                Use os cards abaixo para revisar rapidamente quais fluxos já estão prontos para operação.
              </p>
            </div>
          </div>
        </div>
      </section>

      <motion.section
        variants={staggerContainer(0.06)}
        initial="initial"
        animate="animate"
        className="grid gap-4 md:grid-cols-2 xl:grid-cols-4"
      >
        <StatTile
          eyebrow="Conversas"
          title="Histórico disponível"
          value={String(chats.length).padStart(2, '0')}
          loading={chatsLoading}
          detail={
            recentChats.length > 0
              ? 'Acompanhe sessões recentes e retome fluxos sem perder contexto.'
              : 'Nenhuma sessão iniciada ainda.'
          }
        />
        <StatTile
          eyebrow="Templates"
          title="Fluxos ativos"
          value={String(activeTemplates.length).padStart(2, '0')}
          loading={templatesLoading}
          detail="Templates com versão publicada prontos para serem usados no chat e nas integrações."
        />
        <StatTile
          eyebrow="Uso"
          title="Tokens em 30 dias"
          value={totalTokens > 0 ? `${(totalTokens / 1000).toFixed(1)}k` : '0'}
          loading={usageLoading}
          detail="Leitura direta do endpoint de consumo, reaproveitando a mesma base da tela administrativa."
        />
        <StatTile
          eyebrow="Custo"
          title="Estimativa operacional"
          value={usage ? `US$ ${usage.totals.cost_usd.toFixed(4)}` : 'US$ 0.0000'}
          loading={usageLoading}
          detail={
            usage
              ? `Latência média de ${usage.totals.avg_latency_ms} ms no período.`
              : 'Aguardando dados de consumo do tenant.'
          }
        />
      </motion.section>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Card title="Conversas recentes">
          <div className="space-y-3">
            {chatsLoading ? (
              <>
                <Skeleton className="h-20" />
                <Skeleton className="h-20" />
                <Skeleton className="h-20" />
              </>
            ) : chatsError ? (
              <ErrorText>Não foi possível carregar as conversas recentes.</ErrorText>
            ) : recentChats.length === 0 ? (
              <EmptyState
                title="Nenhuma conversa ainda"
                description="Quando novas conversas forem iniciadas, elas aparecerão aqui com o contexto mais recente."
                action={
                  <Link
                    to="/chat"
                    className="inline-flex min-h-11 items-center rounded-full bg-[var(--brand)] px-4 text-sm font-medium text-white"
                  >
                    Abrir o chat
                  </Link>
                }
              />
            ) : (
              recentChats.map((chat) => (
                <Link
                  key={chat.id}
                  to="/chat"
                  className="block rounded-[20px] border border-[var(--border)] bg-[var(--surface-solid)] px-4 py-4 shadow-[0_12px_40px_-32px_rgba(15,23,42,0.4)] transition hover:border-[var(--brand)]"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-[var(--text)]">
                        {chat.title || 'Nova conversa'}
                      </p>
                      <p className="mt-1 text-sm text-[var(--text-muted)]">
                        {chat.template_id ? 'Template dedicado configurado' : 'Usando template padrão'}
                      </p>
                    </div>
                    <span className="shrink-0 rounded-full border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-1 text-xs text-[var(--text-muted)]">
                      {formatDate(chat.updated_at)}
                    </span>
                  </div>
                </Link>
              ))
            )}
          </div>
        </Card>

        <Card title="Atalhos de templates">
          <div className="space-y-3">
            {templatesLoading ? (
              <>
                <Skeleton className="h-24" />
                <Skeleton className="h-24" />
              </>
            ) : templatesError ? (
              <ErrorText>Não foi possível carregar os templates.</ErrorText>
            ) : featuredTemplates.length === 0 ? (
              <EmptyState
                title="Nenhum template publicado"
                description="Assim que houver versões ativas, este painel exibirá os atalhos."
              />
            ) : (
              featuredTemplates.map((template: TemplateSummary) => (
                <div
                  key={template.id}
                  className="rounded-[20px] border border-[var(--border)] bg-[var(--surface-solid)] px-4 py-4 shadow-[0_12px_40px_-32px_rgba(15,23,42,0.4)]"
                >
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-medium text-[var(--text)]">{template.name}</p>
                    <span className="shrink-0 rounded-full border border-[color-mix(in_srgb,var(--success)_30%,transparent)] bg-[var(--success-soft)] px-2.5 py-1 text-[11px] text-[var(--success)]">
                      v{template.active_version_number}
                    </span>
                  </div>
                  <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">
                    {template.description || 'Template ativo pronto para uso no workspace.'}
                  </p>
                </div>
              ))
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}
