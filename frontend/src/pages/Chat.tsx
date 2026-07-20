import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Button } from '../components/ui'
import { api } from '../lib/api'
import { ArtifactCard, type ArtifactRef } from '../components/ArtifactCard'
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
  const chatIdRef = useRef<string | null>(null)

  const [artifacts, setArtifacts] = useState<ArtifactRef[]>([])
  const [workingAgent, setWorkingAgent] = useState('')
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [recording, setRecording] = useState(false)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    chatIdRef.current = activeChat
    if (!activeChat) {
      setMessages([])
      setArtifacts([])
      return
    }
    api<ChatMessage[]>(`/chats/${activeChat}/messages`).then(setMessages)
    api<ArtifactRef[]>(`/chats/${activeChat}/artifacts`).then(setArtifacts).catch(() => setArtifacts([]))
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
    if ((!text && pendingFiles.length === 0) || busy) return

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
        chatIdRef.current = id
        if (!activeChat) setActiveChat(id)
      },
      onToken: (t) => {
        acc += t
        setStreaming(acc)
      },
      onAgent: (name, status) => {
        setWorkingAgent(status === 'start' ? name : '')
      },
      onArtifact: (artifact) => {
        setArtifacts((a) => [...a, artifact])
      },
      onDone: (full) => {
        setStreaming('')
        setWorkingAgent('')
        setMessages((m) => [
          ...m,
          { id: `done-${Date.now()}`, role: 'assistant', content: full, created_at: '' },
        ])
        // Reload from the API so messages get their database ids (feedback needs them).
        const target = chatIdRef.current
        if (target) {
          api<ChatMessage[]>(`/chats/${target}/messages`).then(setMessages).catch(() => undefined)
        }
      },
      onError: (detail) => {
        setStreaming('')
        setWorkingAgent('')
        setError(detail)
      },
    }, templateId || null, pendingFiles)
    setPendingFiles([])
    setBusy(false)
    qc.invalidateQueries({ queryKey: ['chats'] })
  }


  async function toggleRecording() {
    if (recording) {
      recorderRef.current?.stop()
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream)
      const parts: Blob[] = []
      recorder.ondataavailable = (e) => parts.push(e.data)
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop())
        const blob = new Blob(parts, { type: recorder.mimeType || 'audio/webm' })
        const file = new File([blob], `audio-${Date.now()}.webm`, { type: blob.type })
        setPendingFiles((f) => [...f, file])
        setRecording(false)
      }
      recorderRef.current = recorder
      recorder.start()
      setRecording(true)
    } catch {
      setError('Microfone indisponível')
    }
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
              {m.role === 'assistant' && !m.id.startsWith('done-') && (
                <span className="mt-1 flex gap-1">
                  {[1, -1].map((rating) => (
                    <button
                      key={rating}
                      type="button"
                      title={rating > 0 ? 'Resposta boa' : 'Resposta ruim'}
                      className="text-xs opacity-40 transition hover:opacity-100"
                      onClick={() =>
                        api(`/chats/${chatIdRef.current}/feedback`, {
                          method: 'POST',
                          body: JSON.stringify({ message_id: m.id, rating }),
                        }).catch(() => undefined)
                      }
                    >
                      {rating > 0 ? '👍' : '👎'}
                    </button>
                  ))}
                </span>
              )}
              {(m.attachments ?? []).length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {(m.attachments ?? []).map((a, i) => (
                    <span key={i} className="rounded-full bg-black/20 px-2 py-0.5 text-xs">
                      {a.kind === 'image' ? '🖼' : a.kind === 'audio' ? '🎤' : '📄'} {a.name}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
          {artifacts.map((artifact) => (
            <ArtifactCard key={artifact.artifact_id} artifact={artifact} />
          ))}
          {workingAgent && (
            <p className="text-xs text-slate-500" data-testid="working-agent">
              ⚙ {workingAgent} trabalhando…
            </p>
          )}
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

        {pendingFiles.length > 0 && (
          <div className="flex flex-wrap gap-2 border-t border-slate-800 px-3 pt-2">
            {pendingFiles.map((f, i) => (
              <span key={i} className="flex items-center gap-1 rounded-full bg-slate-800 px-3 py-1 text-xs text-slate-300">
                {f.type.startsWith('image/') ? '🖼' : f.type.startsWith('audio/') ? '🎤' : '📄'} {f.name}
                <button
                  type="button"
                  className="ml-1 text-slate-500 hover:text-rose-400"
                  onClick={() => setPendingFiles(pendingFiles.filter((_, j) => j !== i))}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
        <form onSubmit={onSubmit} className="flex gap-2 border-t border-slate-800 p-3">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            data-testid="chat-file-input"
            accept="image/*,audio/*,.pdf,.docx,.txt,.md,.csv,.xlsx"
            className="hidden"
            onChange={(e) => {
              setPendingFiles([...pendingFiles, ...Array.from(e.target.files ?? [])])
              e.target.value = ''
            }}
          />
          <Button type="button" variant="ghost" onClick={() => fileInputRef.current?.click()} title="Anexar arquivo">
            📎
          </Button>
          <Button
            type="button"
            variant={recording ? 'danger' : 'ghost'}
            onClick={toggleRecording}
            title={recording ? 'Parar gravação' : 'Gravar áudio'}
          >
            {recording ? '⏹' : '🎤'}
          </Button>
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
