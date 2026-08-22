import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useLocation } from 'react-router-dom'
import { ArtifactCard, downloadArtifact, type ArtifactRef } from '../components/ArtifactCard'
import { MarkdownMessage } from '../components/MarkdownMessage'
import { Button } from '../components/ui'
import { api } from '../lib/api'
import { sendMessage, type ChatMessage, type ChatSummary } from '../lib/chat'
import { fadeUp } from '../lib/motion'
import { listTemplates } from '../lib/templates'

function useChats() {
  return useQuery({
    queryKey: ['chats'],
    queryFn: () => api<ChatSummary[]>('/chats'),
  })
}

function formatRelativeDate(value: string) {
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

// Teto do campo de texto: acima disso ele rola por dentro, para um rascunho
// longo não engolir a conversa.
const MAX_COMPOSER_PX = 220

const SIDEBAR_KEY = 'chat.painel.conversas'
const TILES_KEY = 'chat.painel.live-tiles'
const ARTIFACTS_CARD_KEY = 'chat.painel.artefatos'

function conversationTitle(chat: ChatSummary) {
  return chat.title?.trim() || 'Nova conversa'
}

function metricLabel(total: number, singular: string, plural: string) {
  return `${total} ${total === 1 ? singular : plural}`
}

export default function Chat() {
  const location = useLocation()
  const qc = useQueryClient()
  const { data: chats = [] } = useChats()
  const { data: templates = [] } = useQuery({
    queryKey: ['templates'],
    queryFn: listTemplates,
  })

  const [activeChat, setActiveChat] = useState<string | null>(null)
  // Painéis laterais colapsáveis. A preferência fica no navegador: quem
  // escondeu um painel não quer ele de volta a cada recarga.
  const [sidebarOpen, setSidebarOpen] = useState(
    () => localStorage.getItem(SIDEBAR_KEY) !== 'closed',
  )
  const [tilesOpen, setTilesOpen] = useState(
    () => localStorage.getItem(TILES_KEY) !== 'closed',
  )
  // Card de Artefatos tende a crescer muito (um item por artefato numa sessão
  // longa) — toggle próprio, independente do painel inteiro. Começa aberto.
  const [artifactsCardOpen, setArtifactsCardOpen] = useState(
    () => localStorage.getItem(ARTIFACTS_CARD_KEY) !== 'closed',
  )

  useEffect(() => {
    localStorage.setItem(SIDEBAR_KEY, sidebarOpen ? 'open' : 'closed')
  }, [sidebarOpen])
  useEffect(() => {
    localStorage.setItem(TILES_KEY, tilesOpen ? 'open' : 'closed')
  }, [tilesOpen])
  useEffect(() => {
    localStorage.setItem(ARTIFACTS_CARD_KEY, artifactsCardOpen ? 'open' : 'closed')
  }, [artifactsCardOpen])
  const [templateId, setTemplateId] = useState<string>('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [streaming, setStreaming] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [artifacts, setArtifacts] = useState<ArtifactRef[]>([])
  const [workingAgent, setWorkingAgent] = useState('')
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [recording, setRecording] = useState(false)

  const bottomRef = useRef<HTMLDivElement>(null)
  const chatIdRef = useRef<string | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const draftRef = useRef<HTMLTextAreaElement>(null)
  const [highlightedArtifact, setHighlightedArtifact] = useState<string | null>(null)

  async function openRagentesGuide() {
    try {
      const chat = await api<ChatSummary>('/chats/ragentes-guide', { method: 'POST' })
      setTemplateId(chat.template_id ?? '')
      setActiveChat(chat.id)
      setDraft('Olá! Quero entender como configurar meu ambiente.')
      await qc.invalidateQueries({ queryKey: ['chats'] })
    } catch {
      setError('Não foi possível abrir o Assistente RAgentes agora.')
    }
  }

  useEffect(() => {
    if (new URLSearchParams(location.search).get('guide') === '1') {
      void openRagentesGuide()
    }
    // The guide endpoint is idempotent and the URL is the explicit user action.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.search])

  useEffect(() => {
    chatIdRef.current = activeChat
    if (!activeChat) {
      setMessages([])
      setArtifacts([])
      return
    }
    api<ChatMessage[]>(`/chats/${activeChat}/messages`).then(setMessages)
    api<ArtifactRef[]>(`/chats/${activeChat}/artifacts`).then(setArtifacts).catch(() => setArtifacts([]))
    const chat = chats.find((c) => c.id === activeChat)
    if (chat) setTemplateId(chat.template_id ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeChat])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streaming, artifacts, workingAgent])

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
    await sendMessage(
      text,
      activeChat,
      {
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
      },
      templateId || null,
      pendingFiles,
    )
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

  // Link inline `artifact:<id>` embutido pelo modelo na resposta (ux-05):
  // arquivo baixa direto; imagem/dataset abrem o painel e rolam até o card.
  function handleArtifactLink(artifact: ArtifactRef) {
    if (artifact.kind === 'file') {
      downloadArtifact(artifact.artifact_id, artifact.title)
      return
    }
    setTilesOpen(true)
    setArtifactsCardOpen(true)
    setHighlightedArtifact(artifact.artifact_id)
    window.setTimeout(() => {
      document.getElementById(`artifact-${artifact.artifact_id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 50)
    window.setTimeout(() => {
      setHighlightedArtifact((current) => (current === artifact.artifact_id ? null : current))
    }, 1800)
  }

  const activeTemplate = templates.find((t) => t.id === templateId)
  const assistantMessages = messages.filter((m) => m.role === 'assistant').length
  const totalAttachments = messages.reduce((acc, message) => acc + (message.attachments?.length ?? 0), 0) + pendingFiles.length
  const activeChatMeta = chats.find((c) => c.id === activeChat)
  const recentChats = useMemo(() => chats.slice(0, 8), [chats])

  const stats = [
    {
      label: 'Conversas',
      value: String(chats.length).padStart(2, '0'),
      helper: activeChat ? 'Sessão ativa agora' : 'Pronto para iniciar',
    },
    {
      label: 'Respostas',
      value: String(assistantMessages).padStart(2, '0'),
      helper: metricLabel(artifacts.length, 'artefato gerado', 'artefatos gerados'),
    },
    {
      label: 'Contexto',
      value: String(totalAttachments).padStart(2, '0'),
      helper: metricLabel(totalAttachments, 'anexo preparado', 'anexos preparados'),
    },
  ]

  // Classes completas em vez de template string: o Tailwind precisa ver o
  // nome da classe no código-fonte para gerá-la.
  const gridClass = sidebarOpen
    ? tilesOpen
      ? 'lg:grid-cols-[320px_minmax(0,1fr)] xl:grid-cols-[320px_minmax(0,1fr)_320px]'
      : 'lg:grid-cols-[320px_minmax(0,1fr)] xl:grid-cols-[320px_minmax(0,1fr)_56px]'
    : tilesOpen
      ? 'lg:grid-cols-[56px_minmax(0,1fr)] xl:grid-cols-[56px_minmax(0,1fr)_320px]'
      : 'lg:grid-cols-[56px_minmax(0,1fr)] xl:grid-cols-[56px_minmax(0,1fr)_56px]'

  // O campo acompanha o texto: uma linha quando vazio, crescendo até o teto.
  // Antes ele reservava quatro linhas mesmo vazio, e isso saía da conversa.
  useEffect(() => {
    const node = draftRef.current
    if (!node) return
    node.style.height = 'auto'
    node.style.height = `${Math.min(node.scrollHeight, MAX_COMPOSER_PX)}px`
  }, [draft])

  const emptyState = !activeChat && messages.length === 0 && !streaming
  // Conversa de canal externo é histórico: responder por ela exigiria assumir
  // o atendimento no lugar do agente, o que está fora do escopo desta fase.
  const readOnly = (activeChatMeta?.channel ?? 'web') !== 'web'

  return (
    <div
      className={`grid h-full min-h-0 w-full grid-cols-1 overflow-hidden rounded-none border border-[var(--border)] bg-[var(--shell-gradient)] shadow-[0_40px_120px_-60px_rgba(15,23,42,0.65)] sm:rounded-[32px] ${gridClass}`}
    >
      {!sidebarOpen && (
        <aside className="hidden flex-col items-center gap-3 border-r border-[var(--border)] bg-[color-mix(in_srgb,var(--surface-solid)_84%,transparent)] py-4 lg:flex">
          <Button
            type="button"
            variant="ghost"
            data-testid="expandir-conversas"
            aria-label="Mostrar painel de conversas"
            title="Mostrar conversas"
            className="h-10 w-10 rounded-2xl px-0"
            onClick={() => setSidebarOpen(true)}
          >
            »
          </Button>
          <span className="rotate-180 text-[11px] uppercase tracking-[0.2em] text-[var(--text-faint)] [writing-mode:vertical-rl]">
            Conversas
          </span>
        </aside>
      )}

      <aside
        className={`min-h-0 flex-col border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--surface-solid)_84%,transparent)] backdrop-blur-xl lg:border-r lg:border-b-0 ${
          sidebarOpen ? 'flex' : 'flex lg:hidden'
        }`}
      >
        <div className="border-b border-[var(--border)] px-5 py-5">
          <div className="mb-4 flex items-start justify-between gap-3">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-[0.24em] text-[var(--text-faint)]">Workspace de chat</p>
              <h1 className="mt-2 text-xl font-semibold text-[var(--text)]">Central de conversas</h1>
            </div>
            <Button
              type="button"
              variant="ghost"
              data-testid="colapsar-conversas"
              aria-label="Ocultar painel de conversas"
              title="Ocultar conversas"
              className="hidden h-10 w-10 rounded-2xl px-0 lg:inline-flex"
              onClick={() => setSidebarOpen(false)}
            >
              «
            </Button>
          </div>

          <Button
            className="w-full rounded-2xl border-[var(--border)] bg-[var(--surface-soft)] py-3 text-sm text-[var(--text)] shadow-[0_18px_48px_rgba(15,23,42,0.16)] hover:bg-[var(--surface-elevated)]"
            variant="ghost"
            onClick={() => {
              setActiveChat(null)
              setMessages([])
              setArtifacts([])
              setStreaming('')
              setError('')
            }}
          >
            + Nova conversa
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
          <div className="rounded-[24px] border border-[var(--border)] bg-[var(--surface)] p-2 shadow-[0_20px_60px_rgba(15,23,42,0.14)]">
            <div className="mb-2 flex items-center justify-between px-3 pt-2">
              <span className="text-[11px] font-medium uppercase tracking-[0.2em] text-[var(--text-faint)]">Recentes</span>
              <span className="text-xs text-[var(--text-faint)]">{chats.length}</span>
            </div>
            <nav className="space-y-1.5">
              {recentChats.length === 0 && (
                <div className="rounded-2xl border border-dashed border-[var(--border)] px-4 py-6 text-sm text-[var(--text-faint)]">
                  Nenhuma conversa registrada ainda.
                </div>
              )}
              {recentChats.map((c) => {
                const isActive = c.id === activeChat
                return (
                  <button
                    key={c.id}
                    data-testid="chat-item"
                    onClick={() => setActiveChat(c.id)}
                    className={`w-full rounded-2xl border px-4 py-3 text-left transition ${
                      isActive
                        ? 'border-[var(--border-strong)] bg-[var(--surface-elevated)] text-[var(--text)] shadow-[0_18px_48px_rgba(15,23,42,0.12)]'
                        : 'border-transparent bg-transparent text-[var(--text-muted)] hover:border-[var(--border)] hover:bg-[var(--surface-soft)] hover:text-[var(--text)]'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <p className="truncate text-sm font-medium">{conversationTitle(c)}</p>
                      <span className={`h-2.5 w-2.5 rounded-full ${isActive ? 'bg-[var(--success)]' : 'bg-[var(--text-faint)]/40'}`} />
                    </div>
                    <p className={`mt-1 truncate text-xs ${isActive ? 'text-[var(--text-muted)]' : 'text-[var(--text-faint)]'}`}>
                      {c.channel === 'whatsapp'
                        ? `WhatsApp · ${c.external_contact ?? 'contato'}`
                        : c.template_id
                          ? 'Template dedicado'
                          : 'Template padrão'}
                    </p>
                  </button>
                )
              })}
            </nav>
          </div>
        </div>
      </aside>

      <section className="flex min-w-0 flex-col overflow-hidden bg-[var(--panel-gradient)] xl:col-start-2">
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4" data-testid="message-list">
              <div className="mx-auto flex w-full max-w-5xl flex-col gap-4">
                {emptyState && (
                  <section className="rounded-[28px] border border-[var(--border)] bg-[var(--surface)] px-8 py-10 shadow-[0_24px_90px_rgba(15,23,42,0.16)]">
                    <span className="inline-flex rounded-full border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-1 text-xs font-medium uppercase tracking-[0.18em] text-[var(--text-muted)]">
                      Copiloto multiagente
                    </span>
                    <h3 className="mt-5 text-3xl font-semibold tracking-tight text-[var(--text)]">
                      Converse, acione ferramentas e materialize resultados em uma única superfície.
                    </h3>
                    <p className="mt-4 max-w-2xl text-base leading-7 text-[var(--text-muted)]">
                      Use esta área para explorar dados, gerar artefatos e conduzir fluxos operacionais com contexto persistente.
                    </p>
                    <div className="mt-8 grid gap-3 md:grid-cols-3">
                      {[
                        ['Consultas', 'Peça análises, resumos ou revisões guiadas por templates.'],
                        ['Arquivos', 'Anexe PDFs, planilhas, imagens ou áudio como contexto operacional.'],
                        ['Saídas', 'Receba datasets, gráficos e downloads prontos para ação.'],
                      ].map(([title, description]) => (
                        <div key={title} className="rounded-2xl border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-4">
                          <p className="text-sm font-medium text-[var(--text)]">{title}</p>
                          <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">{description}</p>
                        </div>
                      ))}
                    </div>
                    <div className="mt-6 rounded-2xl border border-[var(--brand)]/25 bg-[var(--brand-soft)] p-5">
                      <p className="text-base font-semibold text-[var(--text)]">Precisa de ajuda com a RAgentes?</p>
                      <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--text-muted)]">
                        Entenda a plataforma, veja a configuração do seu ambiente ou monte um novo agente com segurança.
                      </p>
                      <Button type="button" className="mt-4 rounded-2xl" onClick={openRagentesGuide}>
                        Abrir Assistente RAgentes
                      </Button>
                    </div>
                  </section>
                )}

                <AnimatePresence initial={false}>
                {messages.map((m) => {
                  const isUser = m.role === 'user'
                  return (
                    <motion.article
                      key={m.id}
                      variants={fadeUp}
                      initial="initial"
                      animate="animate"
                      className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}
                    >
                      <div
                        className={`max-w-3xl rounded-[24px] border px-5 py-4 shadow-[0_18px_60px_rgba(15,23,42,0.22)] ${
                          isUser
                            ? 'border-[color:var(--brand)]/30 bg-[color:var(--brand)]/90 text-white'
                            : 'border-[var(--border)] bg-[var(--surface)] text-[var(--text)] backdrop-blur'
                        }`}
                      >
                        <div className="flex items-center justify-between gap-4">
                          <span className={`text-[11px] font-medium uppercase tracking-[0.18em] ${isUser ? 'text-white/70' : 'text-[var(--text-faint)]'}`}>
                            {isUser ? 'Você' : 'Assistente'}
                          </span>
                          <span className={`text-xs ${isUser ? 'text-white/60' : 'text-[var(--text-faint)]'}`}>
                            {formatRelativeDate(m.created_at)}
                          </span>
                        </div>
                        {isUser ? (
                          <div className="mt-3 whitespace-pre-wrap text-[15px] leading-7 text-white">{m.content}</div>
                        ) : (
                          <MarkdownMessage
                            content={m.content}
                            artifacts={artifacts}
                            onArtifactLink={handleArtifactLink}
                            className="mt-3 text-[15px] leading-7 text-[var(--text)]"
                          />
                        )}

                        {(m.attachments ?? []).length > 0 && (
                          <div className="mt-4 flex flex-wrap gap-2">
                            {(m.attachments ?? []).map((a, i) => (
                              <span
                                key={i}
                                className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs ${
                                  isUser ? 'bg-white/16 text-white/90' : 'border border-[var(--border)] bg-[var(--surface-soft)] text-[var(--text-muted)]'
                                }`}
                              >
                                {a.kind === 'image' ? 'Imagem' : a.kind === 'audio' ? 'Áudio' : 'Arquivo'} · {a.name}
                              </span>
                            ))}
                          </div>
                        )}

                        {m.role === 'assistant' && !m.id.startsWith('done-') && (
                          <div className="mt-4 flex items-center gap-2">
                            {[1, -1].map((rating) => (
                              <button
                                key={rating}
                                type="button"
                                title={rating > 0 ? 'Resposta boa' : 'Resposta ruim'}
                                className="inline-flex rounded-full border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-1 text-xs text-[var(--text-muted)] transition hover:border-[var(--border-strong)] hover:text-[var(--text)]"
                                onClick={() =>
                                  api(`/chats/${chatIdRef.current}/feedback`, {
                                    method: 'POST',
                                    body: JSON.stringify({ message_id: m.id, rating }),
                                  }).catch(() => undefined)
                                }
                              >
                                {rating > 0 ? 'Útil' : 'Ruim'}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    </motion.article>
                  )
                })}
                </AnimatePresence>

                {streaming && (
                  <motion.article
                    variants={fadeUp}
                    initial="initial"
                    animate="animate"
                    className="flex justify-start"
                  >
                    <div
                      data-testid="streaming-message"
                      className="max-w-3xl rounded-[24px] border border-[var(--info)]/20 bg-[var(--info-soft)] px-5 py-4 text-[15px] leading-7 text-[var(--text)] shadow-[0_18px_60px_rgba(15,23,42,0.18)]"
                    >
                      <div className="mb-3 flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.18em] text-[var(--info)]">
                        Assistente respondendo
                        <span className="inline-block h-2 w-2 rounded-full bg-[var(--info)] animate-pulse" />
                      </div>
                      <MarkdownMessage
                        content={streaming}
                        artifacts={artifacts}
                        onArtifactLink={handleArtifactLink}
                        className="text-[15px] leading-7"
                      />
                    </div>
                  </motion.article>
                )}

                {error && (
                  <p role="alert" className="rounded-2xl border border-[var(--danger)]/20 bg-[var(--danger-soft)] px-4 py-3 text-sm text-[var(--danger)]">
                    {error}
                  </p>
                )}
                <div ref={bottomRef} />
              </div>
            </div>

            <div className="border-t border-[var(--border)] px-6 py-3 backdrop-blur-xl">
              <div className="mx-auto flex w-full max-w-5xl flex-col gap-2 rounded-[24px] border border-[var(--border)] bg-[var(--surface)] p-3 shadow-[0_30px_90px_rgba(15,23,42,0.18)]">
                {pendingFiles.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {pendingFiles.map((f, i) => (
                      <span
                        key={i}
                        className="inline-flex items-center gap-2 rounded-full border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-1.5 text-xs text-[var(--text-muted)]"
                      >
                        {f.type.startsWith('image/') ? 'Imagem' : f.type.startsWith('audio/') ? 'Áudio' : 'Arquivo'} · {f.name}
                        <button
                          type="button"
                          className="text-[var(--text-faint)] transition hover:text-[var(--danger)]"
                          onClick={() => setPendingFiles(pendingFiles.filter((_, j) => j !== i))}
                        >
                          Remover
                        </button>
                      </span>
                    ))}
                  </div>
                )}

                {readOnly && (
                  <p className="rounded-2xl border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-3 text-sm text-[var(--text-muted)]">
                    Conversa do canal {activeChatMeta?.channel} em modo leitura — o agente responde
                    pelo próprio canal.
                  </p>
                )}

                <form onSubmit={onSubmit} className="flex flex-col gap-3">
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

                  <div className="flex flex-1 flex-col gap-2">
                    {/* Uma barra só: ações, estado e envio. Antes eram duas
                        linhas de moldura em volta de um campo de texto. */}
                    <div className="flex flex-wrap items-center gap-2">
                      <Button
                        type="button"
                        variant="ghost"
                        className="rounded-2xl border border-[var(--border)] bg-[var(--surface)] px-3 py-1.5 text-sm text-[var(--text)] hover:bg-[var(--surface-elevated)]"
                        onClick={() => fileInputRef.current?.click()}
                        title="Anexar arquivo"
                      >
                        Anexar arquivo
                      </Button>
                      <Button
                        type="button"
                        variant={recording ? 'danger' : 'ghost'}
                        className="rounded-2xl border border-[var(--border)] px-3 py-1.5 text-sm"
                        onClick={toggleRecording}
                        title={recording ? 'Parar gravação' : 'Gravar áudio'}
                      >
                        {recording ? 'Parar gravação' : 'Gravar áudio'}
                      </Button>
                      <label className="min-w-0">
                        <span className="sr-only">Template ativo</span>
                        <select
                          name="template-picker"
                          value={templateId}
                          onChange={(e) => setTemplateId(e.target.value)}
                          className="max-w-44 rounded-2xl border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text)] outline-none transition focus:border-[var(--brand)] focus:bg-[var(--surface-elevated)]"
                        >
                          <option value="">Template: Padrão</option>
                          {templates.filter((t) => t.active_version_id).map((t) => (
                            <option key={t.id} value={t.id}>{t.name}</option>
                          ))}
                        </select>
                      </label>
                      <span className="hidden text-xs text-[var(--text-faint)] sm:inline">
                        {busy
                          ? workingAgent ? `${workingAgent} trabalhando` : 'Pensando...'
                          : draft.trim().length > 0
                            ? `${draft.trim().length} caracteres`
                            : 'Ocioso'}
                      </span>
                      <Button
                        type="submit"
                        disabled={readOnly || busy || (!draft.trim() && pendingFiles.length === 0)}
                        className="ml-auto rounded-2xl px-5 py-2 text-sm font-semibold shadow-[0_20px_60px_rgba(79,70,229,0.35)]"
                      >
                        {busy ? 'Pensando...' : 'Enviar mensagem'}
                      </Button>
                    </div>
                    <div className="rounded-[20px] border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-2 transition focus-within:border-[var(--brand)] focus-within:bg-[var(--surface-elevated)]">
                      <textarea
                        ref={draftRef}
                        name="chat-input"
                        disabled={readOnly}
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={(e) => {
                          // Enter envia; Shift+Enter quebra linha.
                          if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault()
                            onSubmit(e)
                          }
                        }}
                        placeholder="Descreva a tarefa, faça uma pergunta ou adicione contexto operacional…"
                        autoComplete="off"
                        rows={1}
                        style={{ maxHeight: MAX_COMPOSER_PX }}
                        className="w-full resize-none overflow-y-auto bg-transparent px-1 py-1 text-[15px] leading-7 text-[var(--text)] outline-none placeholder:text-[var(--text-faint)]"
                      />
                    </div>
                  </div>

                </form>
              </div>
            </div>
          </div>
        </div>
      </section>

      {!tilesOpen && (
        <aside className="hidden flex-col items-center gap-3 border-l border-[var(--border)] bg-[color-mix(in_srgb,var(--surface-solid)_72%,transparent)] py-4 xl:flex xl:col-start-3 xl:row-start-1">
          <Button
            type="button"
            variant="ghost"
            data-testid="expandir-live-tiles"
            aria-label="Mostrar painel de indicadores"
            title="Mostrar indicadores"
            className="h-10 w-10 rounded-2xl px-0"
            onClick={() => setTilesOpen(true)}
          >
            «
          </Button>
          <span className="rotate-180 text-[11px] uppercase tracking-[0.2em] text-[var(--text-faint)] [writing-mode:vertical-rl]">
            Live tiles
          </span>
        </aside>
      )}

      <aside
        className={`min-h-0 border-l border-[var(--border)] bg-[color-mix(in_srgb,var(--surface-solid)_72%,transparent)] xl:col-start-3 xl:row-start-1 ${
          tilesOpen ? 'hidden xl:flex xl:flex-col' : 'hidden'
        }`}
      >
        <div className="flex items-start justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
          <div>
            <p className="text-[11px] font-medium uppercase tracking-[0.22em] text-[var(--text-faint)]">Live tiles</p>
            <h3 className="mt-1 text-lg font-semibold text-[var(--text)]">Visão operacional</h3>
          </div>
          <Button
            type="button"
            variant="ghost"
            data-testid="colapsar-live-tiles"
            aria-label="Ocultar painel de indicadores"
            title="Ocultar indicadores"
            className="h-10 w-10 rounded-2xl px-0"
            onClick={() => setTilesOpen(false)}
          >
            »
          </Button>
        </div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {/* Métricas da sessão: vieram da barra lateral, onde ocupavam
              espaço que a lista de conversas usa melhor. */}
          <div className="grid grid-cols-3 gap-2">
            {stats.map((stat) => (
              <div
                key={stat.label}
                data-testid="tile-metrica"
                className="rounded-2xl border border-[var(--border)] bg-[var(--surface)] px-3 py-3"
              >
                <p className="text-[10px] uppercase tracking-[0.16em] text-[var(--text-faint)]">{stat.label}</p>
                <p className="mt-1 text-xl font-semibold text-[var(--text)]">{stat.value}</p>
              </div>
            ))}
          </div>

          <div className="rounded-[24px] border border-[var(--border)] bg-[var(--surface)] p-4">
            <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-faint)]">Status da execução</p>
            <p className="mt-3 text-base font-semibold text-[var(--text)]">{busy ? 'Processando resposta' : 'Aguardando próxima instrução'}</p>
            <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">
              {workingAgent
                ? `${workingAgent} está conduzindo a etapa ativa do fluxo.`
                : 'Nenhum agente especializado está rodando neste instante.'}
            </p>
          </div>

          <div className="rounded-[24px] border border-[var(--border)] bg-[var(--surface)] p-4">
            <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-faint)]">Última conversa</p>
            <p className="mt-3 text-base font-semibold text-[var(--text)]">
              {activeChatMeta ? conversationTitle(activeChatMeta) : 'Ainda não iniciada'}
            </p>
            <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">
              {messages.length > 0
                ? `${messages.length} mensagens na sessão e ${assistantMessages} respostas do assistente.`
                : 'Selecione uma conversa recente ou inicie uma nova sessão.'}
            </p>
          </div>

          <div className="rounded-[24px] border border-[var(--border)] bg-[var(--surface)] p-4">
            <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-faint)]">Template ativo</p>
            <p className="mt-3 text-base font-semibold text-[var(--text)]">{activeTemplate?.name || 'Padrão'}</p>
            <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">
              {activeTemplate?.description || 'Use o seletor acima para aplicar um template com ferramentas e guardrails específicos.'}
            </p>
          </div>

          <div className="rounded-[24px] border border-[var(--border)] bg-[var(--surface)] p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-faint)]">Artefatos da sessão</p>
                <p className="mt-1 text-sm text-[var(--text-muted)]">Saídas geradas pelo fluxo atual.</p>
              </div>
              <div className="flex items-center gap-2">
                <span className="rounded-full border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-1 text-xs text-[var(--text-muted)]">
                  {metricLabel(artifacts.length, 'item', 'itens')}
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  data-testid="colapsar-artefatos"
                  aria-label={artifactsCardOpen ? 'Ocultar artefatos da sessão' : 'Mostrar artefatos da sessão'}
                  aria-expanded={artifactsCardOpen}
                  title={artifactsCardOpen ? 'Ocultar artefatos' : 'Mostrar artefatos'}
                  className="h-8 w-8 rounded-xl px-0"
                  onClick={() => setArtifactsCardOpen((open) => !open)}
                >
                  {artifactsCardOpen ? '︿' : '﹀'}
                </Button>
              </div>
            </div>
            {artifactsCardOpen && (
              <div className="mt-3 grid gap-3">
                {artifacts.length === 0 ? (
                  <p className="rounded-2xl border border-dashed border-[var(--border)] bg-[var(--surface-soft)] px-4 py-6 text-sm text-[var(--text-faint)]">
                    Nenhum artefato gerado ainda nesta sessão.
                  </p>
                ) : (
                  artifacts.map((artifact) => (
                    <ArtifactCard
                      key={artifact.artifact_id}
                      artifact={artifact}
                      highlighted={highlightedArtifact === artifact.artifact_id}
                    />
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      </aside>
    </div>
  )
}
