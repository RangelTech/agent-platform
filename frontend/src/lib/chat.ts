import { getToken } from './api'

export interface ChatSummary {
  id: string
  title: string
  template_id: string | null
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

export interface ArtifactEvent {
  artifact_id: string
  kind: string
  title: string
}

export interface StreamCallbacks {
  onChatId?: (chatId: string) => void
  onToken?: (text: string) => void
  onAgent?: (name: string, status: string) => void
  onArtifact?: (artifact: ArtifactEvent) => void
  onDone?: (fullText: string) => void
  onError?: (detail: string) => void
}

/** POST /api/chat/send and dispatch its SSE events. Returns when the stream ends. */
export async function sendMessage(
  message: string,
  chatId: string | null,
  callbacks: StreamCallbacks,
  templateId?: string | null,
  signal?: AbortSignal,
): Promise<void> {
  const token = getToken()
  const res = await fetch('/api/chat/send', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ message, chat_id: chatId, template_id: templateId ?? null }),
    signal,
  })

  if (!res.ok || !res.body) {
    const detail = await res
      .json()
      .then((b) => b.detail)
      .catch(() => `Erro ${res.status}`)
    callbacks.onError?.(detail)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = ''

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
        let data: Record<string, string>
        try {
          data = JSON.parse(line.slice(6))
        } catch {
          continue
        }
        switch (currentEvent) {
          case 'chat':
            callbacks.onChatId?.(data.chat_id)
            break
          case 'token':
            callbacks.onToken?.(data.text)
            break
          case 'agent':
            callbacks.onAgent?.(data.name, data.status)
            break
          case 'artifact':
            callbacks.onArtifact?.(data as unknown as ArtifactEvent)
            break
          case 'done':
            callbacks.onDone?.(data.text)
            break
          case 'error':
            callbacks.onError?.(data.detail)
            break
        }
      }
    }
  }
}
