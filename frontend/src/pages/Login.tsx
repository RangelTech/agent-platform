import { useState, type FormEvent } from 'react'
import { Button, ErrorText, Input } from '../components/ui'
import { useAuth } from '../lib/auth'

const highlights = [
  {
    eyebrow: 'Deploy',
    title: 'Templates versionados',
    description: 'Supervisores e especialistas com deploy controlado, rollback rápido e governança por tenant.',
  },
  {
    eyebrow: 'Segurança',
    title: 'Dados protegidos',
    description: 'Fontes de dados com allowlist, confirmação de escrita e isolamento operacional por workspace.',
  },
  {
    eyebrow: 'Entrega',
    title: 'Artifacts nativos',
    description: 'Dashboards, planilhas, PDFs e memória operacional produzidos no fluxo real da conversa.',
  },
]

export default function Login() {
  const { signIn } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await signIn(email, password)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Falha no login')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-[image:var(--shell-gradient)] text-[var(--text)] px-4 py-8 sm:px-6 lg:px-8">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-72 bg-[radial-gradient(circle_at_top,var(--brand-soft),transparent_55%)]" />
      <div className="pointer-events-none absolute -left-24 top-24 h-72 w-72 rounded-full bg-[var(--brand-soft)] blur-3xl" />
      <div className="pointer-events-none absolute -right-24 bottom-0 h-80 w-80 rounded-full bg-[var(--info-soft)] blur-3xl" />

      <div className="relative mx-auto grid min-h-[calc(100vh-4rem)] w-full max-w-6xl items-center gap-6 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="hidden rounded-[36px] border border-[var(--border)] bg-[var(--surface)] p-10 shadow-[0_30px_120px_rgba(15,23,42,0.35)] backdrop-blur-xl lg:flex lg:min-h-[720px] lg:flex-col lg:justify-between">
          <div>
            <span className="inline-flex items-center rounded-full border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.2em] text-[var(--text-muted)]">
              Agent Platform
            </span>
            <h1 className="mt-8 max-w-xl text-5xl font-semibold leading-[1.05] tracking-[-0.03em] text-[var(--text)]">
              Orquestre agentes de IA com uma operação realmente pronta para produção.
            </h1>
            <p className="mt-5 max-w-lg text-base leading-7 text-[var(--text-muted)]">
              Centralize templates, serviços de IA, fontes de dados e integrações em um workspace premium para times que exigem velocidade, segurança e governança.
            </p>
          </div>

          <div className="grid gap-4">
            {highlights.map(({ eyebrow, title, description }) => (
              <div key={title} className="rounded-[24px] border border-[var(--border)] bg-[var(--surface-elevated)] p-5 shadow-[0_20px_60px_-40px_rgba(15,23,42,0.85)]">
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-faint)]">{eyebrow}</p>
                <p className="mt-3 text-sm font-semibold text-[var(--text)]">{title}</p>
                <p className="mt-1 text-sm leading-6 text-[var(--text-muted)]">{description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="relative overflow-hidden rounded-[32px] border border-[var(--border)] bg-[image:var(--panel-gradient)] shadow-[0_30px_120px_rgba(15,23,42,0.45)] backdrop-blur-xl">
          <div className="pointer-events-none absolute inset-x-0 top-0 h-40 bg-[radial-gradient(circle_at_top,var(--brand-soft),transparent_70%)]" />
          <div className="relative space-y-8 p-8 sm:p-10 lg:p-12">
            <div className="space-y-4">
              <div className="inline-flex items-center rounded-full border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)] lg:hidden">
                Agent Platform
              </div>
              <div>
                <h2 className="text-3xl font-semibold tracking-tight text-[var(--text)]">Entrar no workspace</h2>
                <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">
                  Faça login para administrar tenants, operar agentes e acompanhar homologações em tempo real.
                </p>
              </div>
              <div className="grid gap-3 sm:grid-cols-3 lg:hidden">
                {highlights.map(({ title }) => (
                  <div key={title} className="rounded-2xl border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-3 text-sm text-[var(--text-muted)]">
                    {title}
                  </div>
                ))}
              </div>
            </div>

            <form onSubmit={onSubmit} className="space-y-5">
              <Input
                label="E-mail"
                hint="Use o e-mail associado ao tenant ou ao administrador master."
                type="email"
                name="email"
                autoComplete="username"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
              <Input
                label="Senha"
                hint="As credenciais seguem o escopo e as permissões do seu workspace."
                type="password"
                name="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <ErrorText>{error}</ErrorText>
              <Button type="submit" disabled={busy} className="w-full">
                {busy ? 'Entrando…' : 'Acessar plataforma'}
              </Button>
            </form>
          </div>
        </section>
      </div>
    </div>
  )
}
