import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Button } from '../components/ui'
import { api } from '../lib/api'
import { sendMessage, type ChatMessage, type ChatSummary } from '../lib/chat'
import { listTemplates } from '../lib/templates'

function useChats() {
  return useQuery({
    queryKey: ['chats'],
    queryFn: () => api<ChatSummary[]>('/chats'),
  })
}

export default function Chat() {
  const qc = useQueryClient()
  const { data: chats = [] } = useChats()
  const { data: templates = [] } = useQuery({
    queryKey: ['templates'],
    queryFn: listTemplates,
  })
  const [activeChat, setActiveChat] = useState<string | null>(null)
  const [templateId, setTemplateId] = useState<string>('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [streaming, setStreaming] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!activeChat) {
      setMessages([])
      return
    }
    api<ChatMessage[]>(`/chats/${activeChat}/messages`).then(setMessages)
    // Reflect the conversation's pinned template in the picker.
    const chat = chats.find((c) => c.id === activeChat)
    if (chat) setTemplateId(chat.template_id ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeChat])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streaming])

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    const text = draft.trim()
    if (!text || busy) return

    setDraft('')
    setError('')
    setBusy(true)
    setMessages((m) => [
      ...m,
      { id: `tmp-${Date.now()}`, role: 'user', content: text, created_at: '' },
    ])

    let acc = ''
    await sendMessage(text, activeChat, {
      onChatId: (id) => {
        if (!activeChat) setActiveChat(id)
      },
      onToken: (t) => {
        acc += t
        setStreaming(acc)
      },
      onDone: (full) => {
        setStreaming('')
        setMessages((m) => [
          ...m,
          { id: `done-${Date.now()}`, role: 'assistant', content: full, created_at: '' },
        ])
      },
      onError: (detail) => {
        setStreaming('')
        setError(detail)
      },
    }, templateId || null)
    setBusy(false)
    qc.invalidateQueries({ queryKey: ['chats'] })
  }

  return (
    <div className="flex h-[calc(100vh-57px)]">
      <aside className="w-64 shrink-0 overflow-y-auto border-r border-slate-800 p-3">
        <Button className="mb-3 w-full" variant="ghost" onClick={() => setActiveChat(null)}>
          + Nova conversa
        </Button>
        <nav className="space-y-1">
          {chats.map((c) => (
            <button
              key={c.id}
              data-testid="chat-item"
              onClick={() => setActiveChat(c.id)}
              className={`block w-full truncate rounded-md px-3 py-2 text-left text-sm ${
                c.id === activeChat
                  ? 'bg-slate-800 text-slate-100'
                  : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
              }`}
            >
              {c.title}
            </button>
          ))}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-slate-800 px-4 py-2">
          <span className="text-xs uppercase tracking-wide text-slate-500">Assistente</span>
          <select
            name="template-picker"
            value={templateId}
            onChange={(e) => setTemplateId(e.target.value)}
            className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-200 outline-none focus:border-indigo-500"
          >
            <option value="">Padrão</option>
            {templates
              .filter((t) => t.active_version_id)
              .map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
          </select>
        </div>
        <div className="flex-1 space-y-4 overflow-y-auto p-4" data-testid="message-list">
          {messages.length === 0 && !streaming && (
            <p className="mt-16 text-center text-sm text-slate-500">
              Envie uma mensagem para começar a conversa.
            </p>
          )}
          {messages.map((m) => (
            <div
              key={m.id}
              className={`max-w-2xl whitespace-pre-wrap rounded-lg px-4 py-2 text-sm ${
                m.role === 'user'
                  ? 'ml-auto bg-indigo-600 text-white'
                  : 'bg-slate-800 text-slate-100'
              }`}
            >
              {m.content}
            </div>
          ))}
          {streaming && (
            <div
              data-testid="streaming-message"
              className="max-w-2xl whitespace-pre-wrap rounded-lg bg-slate-800 px-4 py-2 text-sm text-slate-100"
            >
              {streaming}
              <span className="ml-1 inline-block h-4 w-1.5 animate-pulse bg-slate-400 align-middle" />
            </div>
          )}
          {error && (
            <p role="alert" className="text-sm text-rose-400">
              {error}
            </p>
          )}
          <div ref={bottomRef} />
        </div>

        <form onSubmit={onSubmit} className="flex gap-2 border-t border-slate-800 p-3">
          <input
            name="chat-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Escreva sua mensagem…"
            autoComplete="off"
            className="flex-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-indigo-500"
          />
          <Button type="submit" disabled={busy || !draft.trim()}>
            {busy ? 'Enviando…' : 'Enviar'}
          </Button>
        </form>
      </div>
    </div>
  )
}
