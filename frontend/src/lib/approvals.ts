import { api } from './api'

export type Risk = 'low' | 'medium' | 'high'

export interface PendingApproval {
  approval_id: string
  chat_id?: string
  summary: string
  risk: Risk
  kind?: string
  details?: Record<string, unknown>
  expires_at: string
}

export type Decision = 'approve' | 'deny'
export type ApprovalStatus = 'approved' | 'denied' | 'expired'

export const listPendingApprovals = () => api<PendingApproval[]>('/approvals/pending')
export const decideApproval = (id: string, decision: Decision) =>
  api<{ status: ApprovalStatus }>(`/approvals/${id}`, {
    method: 'POST',
    body: JSON.stringify({ decision }),
  })

export const RISK_LABEL: Record<Risk, string> = {
  low: 'Risco baixo',
  medium: 'Risco médio',
  high: 'Risco alto',
}
