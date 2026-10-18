import { ShieldAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ApiError } from '../lib/api'
import { decideApproval, RISK_LABEL, type ApprovalStatus, type Risk } from '../lib/approvals'
import { Button } from './ui'

export interface ApprovalView {
  approval_id: string
  summary: string
  risk: Risk
  expires_at: string
  /** Resultado já conhecido (decidido, expirado) */
  status?: ApprovalStatus
}

const RISK_TONE: Record<Risk, string> = {
  low: 'bg-[var(--success-soft)] text-[var(--success)]',
  medium: 'bg-[var(--info-soft)] text-[var(--info)]',
  high: 'bg-[var(--danger-soft)] text-[var(--danger)]',
}

const RESULT_TEXT: Record<ApprovalStatus, string> = {
  approved: 'Você aprovou esta ação.',
  denied: 'Você negou esta ação.',
  expired: 'O prazo para confirmar acabou. A ação não foi executada.',
}

function remainingSeconds(expiresAt: string): number {
  const end = new Date(expiresAt).getTime()
  if (Number.isNaN(end)) return 0
  return Math.max(0, Math.round((end - Date.now()) / 1000))
}

/**
 * Cartão de confirmação inline no chat. Só o clique do usuário autenticado
 * executa a ação (POST /approvals/{id}); o prazo vence sozinho.
 */
export function ApprovalCard({
  approval,
  onResolved,
}: {
  approval: ApprovalView
  onResolved: (id: string, status: ApprovalStatus) => void
}) {
  const [left, setLeft] = useState(() => remainingSeconds(approval.expires_at))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const status = approval.status

  useEffect(() => {
    if (status) return
    const timer = window.setInterval(() => {
      const next = remainingSeconds(approval.expires_at)
      setLeft(next)
      if (next <= 0) {
        window.clearInterval(timer)
        onResolved(approval.approval_id, 'expired')
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [approval.approval_id, approval.expires_at, status, onResolved])

  async function decide(decision: 'approve' | 'deny') {
    setBusy(true)
    setError('')
    try {
      const res = await decideApproval(approval.approval_id, decision)
      onResolved(approval.approval_id, res.status)
    } catch (e) {
      setError(
        e instanceof ApiError && e.message
          ? e.message
          : 'Não foi possível registrar sua decisão. Tente de novo.',
      )
    } finally {
      setBusy(false)
    }
  }

  const mm = String(Math.floor(left / 60)).padStart(2, '0')
  const ss = String(left % 60).padStart(2, '0')

  return (
    <article
      data-testid="approval-card"
      data-status={status ?? 'pending'}
      data-risk={approval.risk}
      aria-label="Confirmação necessária"
      className="max-w-3xl rounded-[24px] border border-[var(--border-strong)] bg-[var(--surface)] px-5 py-4 shadow-[0_18px_60px_rgba(15,23,42,0.18)]"
    >
      <div className="flex flex-wrap items-center gap-3">
        <ShieldAlert size={18} aria-hidden="true" className="text-[var(--text-muted)]" />
        <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
          Aguardando sua confirmação
        </span>
        <span
          data-testid="approval-risk"
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${RISK_TONE[approval.risk]}`}
        >
          {RISK_LABEL[approval.risk]}
        </span>
      </div>
      <p data-testid="approval-summary" className="mt-3 text-[15px] leading-7 text-[var(--text)]">
        {approval.summary}
      </p>
      {status ? (
        <p data-testid="approval-result" className="mt-3 text-sm text-[var(--text-muted)]">
          {RESULT_TEXT[status]}
        </p>
      ) : (
        <>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button type="button" disabled={busy} data-testid="approval-approve" onClick={() => decide('approve')}>
              Aprovar
            </Button>
            <Button
              type="button"
              variant="danger"
              disabled={busy}
              data-testid="approval-deny"
              onClick={() => decide('deny')}
            >
              Negar
            </Button>
            <span data-testid="approval-timer" className="text-xs text-[var(--text-faint)]">
              Expira em {mm}:{ss}
            </span>
          </div>
          {error && (
            <p role="alert" className="mt-3 text-sm text-[var(--danger)]">
              {error}
            </p>
          )}
        </>
      )}
    </article>
  )
}
