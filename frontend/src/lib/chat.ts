import { api, getToken, statusMessage } from './api'

export interface ChatSummary {
  id: string
  title: string
  template_id: string | null
  /** Tipo do agente do chat (fixado no primeiro envio). */
  agent_kind?: 'structured' | 'skills' | null
  /** 'web' para conversas da UI; 'whatsapp' para conversas vindas do canal. */
  channel?: string
  external_contact?: string | null
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  attachments?: { kind: string; name: string }[]
  created_at: string
}

export interface ArtifactEvent {
  artifact_id: string
  kind: string
  title: string
}

export interface StepEvent {
  kind: 'skill' | 'tool' | 'plan' | 'queue' | 'wake'
  label: string
  status: 'start' | 'done' | 'error'
  position?: number
}

export interface ApprovalEvent {
  approval_id: string
  summary: string
  risk: 'low' | 'medium' | 'high'
  expires_at: string
}

export interface SkillProposedEvent {
  user_skill_id: string
  name: string
  status: string
}

export interface UsageEvent {
  input_tokens: number
  output_tokens: number
}

export interface StreamCallbacks {
  onChatId?: (chatId: string) => void
  /** Id da execução em andamento (para o botão Parar). */
  onRunId?: (runId: string) => void
  onToken?: (text: string) => void
  onAgent?: (name: string, status: string) => void
  onArtifact?: (artifact: ArtifactEvent) => void
  onStep?: (step: StepEvent) => void
  onApprovalRequired?: (approval: ApprovalEvent) => void
  onSkillProposed?: (skill: SkillProposedEvent) => void
  onUsage?: (usage: UsageEvent) => void
  onDone?: (fullText: string) => void
  onError?: (detail: string, status?: number) => void
  /** O usuário parou a execução (AbortController). */
  onAbort?: () => void
}

export const CONFLICT_MESSAGE =
  'Este chat usa outro tipo de agente. Abra uma nova conversa para usar este agente.'

/** POST /api/chat/runs/{run_id}/cancel */
export const cancelRun = (runId: string) =>
  api<{ status: string }>(`/chat/runs/${runId}/cancel`, { method: 'POST' })

/** POST /api/chat/send and dispatch its SSE events. Returns when the stream ends. */
export async function sendMessage(
  message: string,
  chatId: string | null,
  callbacks: StreamCallbacks,
  templateId?: string | null,
  attachments?: File[],
  signal?: AbortSignal,
): Promise<void> {
  const token = getToken()
  let body: BodyInit
  const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}

  if (attachments && attachments.length > 0) {
    const form = new FormData()
    form.append('message', message)
    if (chatId) form.append('chat_id', chatId)
    if (templateId) form.append('template_id', templateId)
    for (const file of attachments) form.append('files', file)
    body = form // browser sets the multipart boundary
  } else {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify({ message, chat_id: chatId, template_id: templateId ?? null })
  }

  let res: Response
  try {
    res = await fetch('/api/chat/send', { method: 'POST', headers, body, signal })
  } catch (err) {
    if (signal?.aborted) callbacks.onAbort?.()
    else callbacks.onError?.('Não foi possível falar com o servidor. Verifique sua conexão e tente de novo.')
    void err
    return
  }

  if (!res.ok || !res.body) {
    const detail = await res
      .json()
      .then((b) => b.detail)
      .catch(() => null)
    const text =
      typeof detail === 'string' && detail
        ? detail
        : res.status === 409
          ? CONFLICT_MESSAGE
          : statusMessage(res.status)
    callbacks.onError?.(text, res.status)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      let idx: number
      while ((idx = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, idx).trimEnd()
        buffer = buffer.slice(idx + 1)

        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7)
        } else if (line.startsWith('data: ')) {
          let data: Record<string, unknown>
          try {
            data = JSON.parse(line.slice(6))
          } catch {
            continue
          }
          dispatch(currentEvent, data, callbacks)
        }
      }
    }
  } catch (err) {
    if (signal?.aborted || (err instanceof DOMException && err.name === 'AbortError')) {
      callbacks.onAbort?.()
    } else {
      callbacks.onError?.('A conexão com o servidor foi interrompida. Envie a mensagem de novo.')
    }
  }
}

function dispatch(event: string, data: Record<string, unknown>, cb: StreamCallbacks) {
  switch (event) {
    case 'chat':
      cb.onChatId?.(data.chat_id as string)
      if (typeof data.run_id === 'string') cb.onRunId?.(data.run_id)
      break
    case 'token':
      cb.onToken?.(data.text as string)
      break
    case 'agent':
      cb.onAgent?.(data.name as string, data.status as string)
      break
    case 'artifact':
      cb.onArtifact?.(data as unknown as ArtifactEvent)
      break
    case 'step':
      cb.onStep?.(data as unknown as StepEvent)
      break
    case 'approval_required':
      cb.onApprovalRequired?.(data as unknown as ApprovalEvent)
      break
    case 'skill_proposed':
      cb.onSkillProposed?.(data as unknown as SkillProposedEvent)
      break
    case 'usage':
      cb.onUsage?.(data as unknown as UsageEvent)
      break
    case 'done':
      cb.onDone?.(data.text as string)
      break
    case 'error':
      cb.onError?.(data.detail as string)
      break
    default:
      // Tipos desconhecidos são ignorados de propósito (contrato evolui).
      break
  }
}

/** Texto discreto para cada etapa informada pelo agente. */
export function stepText(step: StepEvent): string {
  if (step.kind === 'queue') return step.position ? `Na fila, posição ${step.position}` : 'Na fila'
  if (step.kind === 'skill') return `Consultando: ${step.label}`
  if (step.kind === 'tool') return `Usando: ${step.label}`
  if (step.kind === 'wake') return 'Preparando o ambiente do agente'
  return step.label || 'Planejando'
}
