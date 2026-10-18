import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { FileTree } from '../components/FileTree'
import { FrontmatterForm } from '../components/FrontmatterForm'
import { MarkdownEditor } from '../components/MarkdownEditor'
import { VersionsCard } from '../components/VersionsCard'
import {
  Badge,
  Button,
  Chip,
  ConfirmDialog,
  ErrorText,
  Input,
  Modal,
  PageHeader,
  Segmented,
  Tabs,
  useToast,
} from '../components/ui'
import { ApiError } from '../lib/api'
import { useAuth } from '../lib/auth'
import { sendMessage, stepText, type StepEvent } from '../lib/chat'
import { AGENT_MD_TEMPLATE, newSkillTemplate, stripFrontmatter } from '../lib/frontmatter'
import {
  EMPTY_MANIFEST,
  getDraft,
  getVersionFile,
  listSkillVersions,
  listVersionFiles,
  PROBLEM_TITLE,
  publishDraft,
  saveDraft,
  toolsCatalog,
  validateDraft,
  type Manifest,
  type Problem,
  type SkillFile,
} from '../lib/skills'
import { deployVersion, listTemplates } from '../lib/templates'

type PanelTab = 'preview' | 'problems' | 'manifest' | 'test'
type MobilePane = 'files' | 'editor' | 'panel'
type ValidState = 'idle' | 'running' | 'done' | 'failed'

interface PathDialogState {
  mode: 'new-file' | 'new-folder' | 'rename'
  kind: 'file' | 'folder'
  from?: string
  initial: string
}

const backupKey = (id: string) => `skills-draft:${id}`

function snapshotKey(files: SkillFile[], manifest: Manifest) {
  return JSON.stringify([[...files].sort((a, b) => a.path.localeCompare(b.path)).map((f) => [f.path, f.content]), manifest])
}

function validatePathInput(path: string, existing: string[], from?: string): string {
  const p = path.trim()
  if (!p) return 'Informe o caminho.'
  if (p.startsWith('/') || p.includes('..') || p.split('/').some((s) => s.startsWith('.')))
    return 'Use um caminho relativo, sem “..” nem pastas ocultas.'
  if (/\s/.test(p)) return 'Não use espaços no caminho.'
  if (p.endsWith('/')) return 'O caminho não pode terminar com barra.'
  if (p !== from && existing.includes(p)) return 'Já existe um arquivo com esse caminho.'
  return ''
}

export default function SkillsTemplateEditor() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [sp, setSp] = useSearchParams()
  const qc = useQueryClient()
  const toast = useToast()
  const { can, user } = useAuth()
  const skillsEnabled = Boolean(user?.is_master || user?.skills_agents_enabled)
  const readOnly = !can('templates', 'edit')

  const { data: templates = [], isLoading: loadingTemplates } = useQuery({ queryKey: ['templates'], queryFn: listTemplates })
  const template = templates.find((t) => t.id === id)
  const draftQ = useQuery({
    queryKey: ['skills-draft', id],
    queryFn: () => getDraft(id),
    enabled: skillsEnabled && Boolean(id),
    staleTime: 0,
    gcTime: 0,
  })
  const catalogQ = useQuery({ queryKey: ['tools-catalog'], queryFn: toolsCatalog, staleTime: 5 * 60_000, enabled: skillsEnabled })
  const versionsQ = useQuery({
    queryKey: ['skills-versions', id],
    queryFn: () => listSkillVersions(id),
    enabled: skillsEnabled && Boolean(id),
  })

  const [files, setFiles] = useState<SkillFile[]>([])
  const [folders, setFolders] = useState<string[]>([])
  const [manifest, setManifest] = useState<Manifest>(EMPTY_MANIFEST)
  const [saved, setSaved] = useState<{ files: Record<string, string>; manifest: string } | null>(null)
  const [problems, setProblems] = useState<Problem[]>([])
  const [validState, setValidState] = useState<ValidState>('idle')
  const [validatedKey, setValidatedKey] = useState('')
  const [validationError, setValidationError] = useState('')
  const [panelTab, setPanelTab] = useState<PanelTab>('preview')
  const [mobilePane, setMobilePane] = useState<MobilePane>('editor')
  const [pathDialog, setPathDialog] = useState<PathDialogState | null>(null)
  const [pathValue, setPathValue] = useState('')
  const [pathError, setPathError] = useState('')
  const [deleting, setDeleting] = useState<{ path: string; kind: 'file' | 'folder' } | null>(null)
  const [newSkillOpen, setNewSkillOpen] = useState(false)
  const [newSkillName, setNewSkillName] = useState('')
  const [newSkillError, setNewSkillError] = useState('')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')
  const [backup, setBackup] = useState<{ files: SkillFile[]; manifest: Manifest } | null>(null)
  const [restoring, setRestoring] = useState<string | null>(null)
  const [loadFromVersion, setLoadFromVersion] = useState<string | null>(null)

  // ---- carga do rascunho (uma vez por resposta) --------------------------------
  const initializedFor = useRef<string | null>(null)
  useEffect(() => {
    const d = draftQ.data
    if (!d || initializedFor.current === `${id}:${draftQ.dataUpdatedAt}`) return
    initializedFor.current = `${id}:${draftQ.dataUpdatedAt}`
    const serverFiles = d.files.map((f) => ({ path: f.path, content: f.content }))
    const serverManifest = { ...EMPTY_MANIFEST, ...d.manifest }
    setFiles(serverFiles)
    setFolders([])
    setManifest(serverManifest)
    setSaved({
      files: Object.fromEntries(serverFiles.map((f) => [f.path, f.content])),
      manifest: JSON.stringify(serverManifest),
    })
    setProblems([])
    setValidState('idle')
    try {
      const raw = localStorage.getItem(backupKey(id))
      if (raw) {
        const parsed = JSON.parse(raw) as { files: SkillFile[]; manifest: Manifest }
        if (snapshotKey(parsed.files, parsed.manifest) !== snapshotKey(serverFiles, serverManifest)) setBackup(parsed)
        else localStorage.removeItem(backupKey(id))
      }
    } catch {
      /* armazenamento indisponível: segue sem cópia local */
    }
  }, [draftQ.data, draftQ.dataUpdatedAt, id])

  // ---- derivados ---------------------------------------------------------------
  const filePaths = useMemo(() => files.map((f) => f.path), [files])
  const dirtyPaths = useMemo(() => {
    const s = new Set<string>()
    if (!saved) return s
    for (const f of files) if (saved.files[f.path] !== f.content) s.add(f.path)
    return s
  }, [files, saved])
  const removedCount = useMemo(
    () => (saved ? Object.keys(saved.files).filter((p) => !filePaths.includes(p)).length : 0),
    [saved, filePaths],
  )
  const manifestDirty = saved ? JSON.stringify(manifest) !== saved.manifest : false
  const dirty = dirtyPaths.size > 0 || removedCount > 0 || manifestDirty

  const activeFromUrl = sp.get('file')
  const activePath =
    activeFromUrl && filePaths.includes(activeFromUrl)
      ? activeFromUrl
      : filePaths.includes('AGENTE.md')
        ? 'AGENTE.md'
        : (filePaths[0] ?? null)
  const activeFile = files.find((f) => f.path === activePath) ?? null

  const problemMap = useMemo(() => {
    const m = new Map<string, 'error' | 'warning'>()
    for (const p of problems) {
      if (!p.path) continue
      if (p.severity === 'error' || !m.has(p.path)) m.set(p.path, p.severity)
    }
    return m
  }, [problems])
  const errorCount = problems.filter((p) => p.severity === 'error').length
  const warningCount = problems.filter((p) => p.severity === 'warning').length
  const currentKey = useMemo(() => snapshotKey(files, manifest), [files, manifest])
  const validationFresh = validState === 'done' && validatedKey === currentKey

  const activeVersionId = template?.active_version_id ?? versionsQ.data?.find((v) => v.is_active)?.id ?? null

  // ---- cópia local e aviso de saída --------------------------------------------
  useEffect(() => {
    if (!saved || !id) return
    try {
      if (dirty) localStorage.setItem(backupKey(id), JSON.stringify({ files, manifest }))
      else localStorage.removeItem(backupKey(id))
    } catch {
      /* ignorado */
    }
  }, [dirty, files, manifest, id, saved])
  useEffect(() => {
    if (!dirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  // ---- validação ---------------------------------------------------------------
  const runValidation = useCallback(
    async (f: SkillFile[], m: Manifest): Promise<boolean> => {
      setValidState('running')
      setValidationError('')
      try {
        const res = await validateDraft(id, f, m)
        setProblems(res.problems)
        setValidatedKey(snapshotKey(f, m))
        setValidState('done')
        return !res.problems.some((p) => p.severity === 'error')
      } catch (e) {
        setValidState('failed')
        setValidationError(e instanceof Error ? e.message : 'Não foi possível validar agora.')
        return false
      }
    },
    [id],
  )

  useEffect(() => {
    if (!saved || readOnly) return
    const handle = window.setTimeout(() => {
      void runValidation(files, manifest)
    }, 700)
    return () => window.clearTimeout(handle)
    // currentKey resume files+manifest
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentKey, saved, readOnly])

  // ---- operações de arquivo ----------------------------------------------------
  function selectFile(path: string) {
    const next = new URLSearchParams(sp)
    next.set('file', path)
    next.delete('newSkill')
    setSp(next, { replace: true })
    setMobilePane('editor')
  }

  function updateContent(path: string, content: string) {
    setFiles((list) => list.map((f) => (f.path === path ? { ...f, content } : f)))
  }

  function openPathDialog(state: PathDialogState) {
    setPathDialog(state)
    setPathValue(state.initial)
    setPathError('')
  }

  function seedFor(path: string): string {
    if (path === 'AGENTE.md') return AGENT_MD_TEMPLATE
    const m = /^skills\/([^/]+)\/SKILL\.md$/.exec(path)
    return m ? newSkillTemplate(m[1]) : ''
  }

  function submitPath(e: FormEvent) {
    e.preventDefault()
    if (!pathDialog) return
    const value = pathValue.trim()
    if (pathDialog.mode === 'new-folder') {
      const err = value ? validatePathInput(value, [], undefined) : 'Informe o nome da pasta.'
      if (err) return setPathError(err)
      setFolders((list) => (list.includes(value) ? list : [...list, value]))
      setPathDialog(null)
      return
    }
    if (pathDialog.mode === 'new-file') {
      const err = validatePathInput(value, filePaths)
      if (err) return setPathError(err)
      setFiles((list) => [...list, { path: value, content: seedFor(value) }])
      setPathDialog(null)
      selectFile(value)
      return
    }
    // renomear / mover
    const from = pathDialog.from!
    if (pathDialog.kind === 'file') {
      const err = validatePathInput(value, filePaths, from)
      if (err) return setPathError(err)
      setFiles((list) => list.map((f) => (f.path === from ? { ...f, path: value } : f)))
      setPathDialog(null)
      if (activePath === from) selectFile(value)
      return
    }
    const err = value ? validatePathInput(value, [], undefined) : 'Informe o novo caminho da pasta.'
    if (err) return setPathError(err)
    const prefix = `${from}/`
    const moved = files.map((f) => (f.path.startsWith(prefix) ? { ...f, path: `${value}/${f.path.slice(prefix.length)}` } : f))
    if (new Set(moved.map((f) => f.path)).size !== moved.length) return setPathError('Já existe um arquivo com esse caminho.')
    setFiles(moved)
    setFolders((list) => list.map((p) => (p === from ? value : p.startsWith(prefix) ? `${value}/${p.slice(prefix.length)}` : p)))
    setPathDialog(null)
    if (activePath?.startsWith(prefix)) selectFile(`${value}/${activePath.slice(prefix.length)}`)
  }

  function confirmDelete() {
    if (!deleting) return
    if (deleting.kind === 'file') {
      setFiles((list) => list.filter((f) => f.path !== deleting.path))
    } else {
      const prefix = `${deleting.path}/`
      setFiles((list) => list.filter((f) => !f.path.startsWith(prefix)))
      setFolders((list) => list.filter((p) => p !== deleting.path && !p.startsWith(prefix)))
    }
    setDeleting(null)
  }

  function submitNewSkill(e: FormEvent) {
    e.preventDefault()
    const name = newSkillName.trim()
    if (!/^[a-z0-9][a-z0-9._-]*$/.test(name) || name.length > 64) {
      return setNewSkillError('Use minúsculas, números, ponto, hífen ou sublinhado (até 64 caracteres), começando por letra ou número.')
    }
    const path = `skills/${name}/SKILL.md`
    if (filePaths.includes(path)) return setNewSkillError('Já existe uma skill com esse nome.')
    setFiles((list) => [...list, { path, content: newSkillTemplate(name) }])
    setNewSkillOpen(false)
    setNewSkillName('')
    setNewSkillError('')
    selectFile(path)
  }

  // Atalho vindo da tela Skills: abre o diálogo de nova skill.
  const newSkillRequested = sp.get('newSkill') === '1'
  useEffect(() => {
    if (newSkillRequested && saved && !readOnly) {
      setNewSkillOpen(true)
      const next = new URLSearchParams(sp)
      next.delete('newSkill')
      setSp(next, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newSkillRequested, saved, readOnly])

  // ---- salvar / publicar / restaurar -------------------------------------------
  const save = useMutation({
    mutationFn: () => saveDraft(id, files, manifest),
    onSuccess: (res) => {
      setError('')
      setSaved({ files: Object.fromEntries(files.map((f) => [f.path, f.content])), manifest: JSON.stringify(manifest) })
      setProblems(res.problems ?? [])
      setValidatedKey(currentKey)
      setValidState('done')
      toast('Rascunho salvo.')
    },
    onError: (e: Error) => {
      setError(e.message)
      toast(e.message, 'error')
    },
  })

  const publish = useMutation({
    mutationFn: async () => {
      await saveDraft(id, files, manifest)
      const version = await publishDraft(id, notes.trim())
      await deployVersion(id, version.id)
      return version
    },
    onSuccess: (version) => {
      setError('')
      setNotes('')
      setSaved({ files: Object.fromEntries(files.map((f) => [f.path, f.content])), manifest: JSON.stringify(manifest) })
      toast(`Versão v${version.number} publicada e em produção.`)
      qc.invalidateQueries({ queryKey: ['templates'] })
      qc.invalidateQueries({ queryKey: ['skills-versions', id] })
    },
    onError: (e: Error) => {
      if (e instanceof ApiError && e.status === 422) {
        const body = e.body as { problems?: Problem[]; detail?: { problems?: Problem[] } } | null
        const list = body?.problems ?? body?.detail?.problems
        if (list) {
          setProblems(list)
          setValidState('done')
          setValidatedKey(currentKey)
          setPanelTab('problems')
        }
        setError('O servidor recusou a publicação: corrija os problemas listados.')
        return
      }
      setError(e.message)
      toast(e.message, 'error')
    },
  })

  const restore = useMutation({
    mutationFn: (versionId: string) => deployVersion(id, versionId),
    onSuccess: () => {
      toast('Versão restaurada e em produção.')
      qc.invalidateQueries({ queryKey: ['templates'] })
      qc.invalidateQueries({ queryKey: ['skills-versions', id] })
    },
    onError: (e: Error) => toast(e.message, 'error'),
  })

  async function loadVersionIntoDraft(versionId: string) {
    try {
      const list = await listVersionFiles(id, versionId)
      const loaded: SkillFile[] = []
      for (const item of list) {
        const f = await getVersionFile(id, versionId, item.path)
        loaded.push({ path: f.path, content: f.content })
      }
      setFiles(loaded)
      setFolders([])
      toast('Versão aberta no rascunho. Salve para guardar.', 'info')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Não foi possível abrir a versão.', 'error')
    }
  }

  const publishBlocked = !validationFresh || errorCount > 0 || publish.isPending || save.isPending
  const publishReason = !validationFresh
    ? validState === 'failed'
      ? 'Não foi possível validar. Tente validar de novo.'
      : 'Validando o pacote…'
    : errorCount > 0
      ? `Corrija ${errorCount} ${errorCount === 1 ? 'erro' : 'erros'} para publicar.`
      : ''

  // ---- guardas -----------------------------------------------------------------
  if (!skillsEnabled) {
    return (
      <div className="space-y-6">
        <PageHeader title="Agente por skills" />
        <ErrorText>Recurso não habilitado para sua empresa.</ErrorText>
      </div>
    )
  }
  if (loadingTemplates || draftQ.isLoading) {
    return (
      <div className="space-y-6" data-testid="skills-editor-loading">
        <PageHeader title="Agente por skills" />
        <p className="text-sm text-[var(--text-muted)]">Carregando…</p>
      </div>
    )
  }
  if (!template || draftQ.error) {
    return (
      <div className="space-y-6" data-testid="skills-editor-error">
        <PageHeader
          title="Agente por skills"
          actions={
            <Button variant="ghost" onClick={() => navigate('/templates')}>
              ← Voltar
            </Button>
          }
        />
        <ErrorText>
          {draftQ.error instanceof Error && draftQ.error.message
            ? draftQ.error.message
            : 'Agente não encontrado. Ele pode ter sido arquivado.'}
        </ErrorText>
      </div>
    )
  }

  const catalog = catalogQ.data ?? []
  const isSkillMd = activePath ? /(^|\/)SKILL\.md$/.test(activePath) : false
  const statusBadge = dirty ? (
    <span
      data-testid="unsaved-indicator"
      className="inline-flex items-center gap-1.5 rounded-full bg-[var(--info-soft)] px-2.5 py-1 text-xs font-medium text-[var(--info)]"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-[var(--info)]" />
      Alterações não salvas
    </span>
  ) : (
    <span data-testid="saved-indicator" className="text-xs text-[var(--text-faint)]">
      Tudo salvo
    </span>
  )

  const panelItems = [
    { id: 'preview', label: 'Prévia', testId: 'panel-tab-preview' },
    { id: 'problems', label: 'Problemas', testId: 'panel-tab-problems', count: problems.length },
    { id: 'manifest', label: 'Manifesto', testId: 'panel-tab-manifest' },
    { id: 'test', label: 'Testar', testId: 'panel-tab-test' },
  ]

  return (
    <div className="space-y-5" data-testid="skills-editor">
      <PageHeader
        title={template.name}
        description={
          activeVersionId
            ? `v${template.active_version_number ?? versionsQ.data?.find((v) => v.is_active)?.number ?? ''} em produção`.trim()
            : 'Ainda sem versão publicada'
        }
        actions={
          <>
            <Button variant="ghost" data-testid="editor-back" onClick={() => navigate('/templates')}>
              ← Voltar
            </Button>
            {!readOnly && (
              <>
                <Button
                  variant="ghost"
                  data-testid="skill-validate"
                  disabled={validState === 'running'}
                  onClick={() => {
                    setPanelTab('problems')
                    setMobilePane('panel')
                    void runValidation(files, manifest)
                  }}
                >
                  Validar
                </Button>
                <Button
                  variant="ghost"
                  data-testid="skill-save-draft"
                  disabled={save.isPending || !dirty}
                  onClick={() => save.mutate()}
                >
                  Salvar rascunho
                </Button>
                <Button
                  data-testid="skill-publish"
                  disabled={publishBlocked}
                  title={publishReason || undefined}
                  onClick={() => publish.mutate()}
                >
                  Publicar
                </Button>
              </>
            )}
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-3">
        <Badge ok={false}>Rascunho</Badge>
        {statusBadge}
        {publishReason && !readOnly && (
          <span data-testid="publish-blocked-reason" className="text-xs text-[var(--text-muted)]">
            {publishReason}
          </span>
        )}
        {!readOnly && (
          <label className="ml-auto flex min-w-0 flex-1 items-center gap-2 sm:w-80 sm:flex-none">
            <span className="sr-only">Notas da versão</span>
            <input
              name="version-notes"
              data-testid="version-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Notas da versão (opcional)"
              className="w-full rounded-2xl border border-[var(--border)] bg-[var(--surface-solid)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--brand)]"
            />
          </label>
        )}
      </div>

      {readOnly && (
        <p data-testid="readonly-note" className="rounded-2xl border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-3 text-sm text-[var(--text-muted)]">
          Seu perfil pode ver este agente, mas não editar.
        </p>
      )}

      {backup && (
        <div
          data-testid="local-backup-banner"
          role="status"
          className="flex flex-wrap items-center gap-3 rounded-2xl border border-[var(--info)]/40 bg-[var(--info-soft)] px-4 py-3 text-sm text-[var(--text)]"
        >
          <span className="min-w-0 flex-1">Encontramos alterações deste agente guardadas neste navegador que não foram salvas.</span>
          <Button
            variant="ghost"
            data-testid="local-backup-restore"
            onClick={() => {
              setFiles(backup.files)
              setManifest({ ...EMPTY_MANIFEST, ...backup.manifest })
              setBackup(null)
            }}
          >
            Recuperar
          </Button>
          <Button
            variant="ghost"
            data-testid="local-backup-discard"
            onClick={() => {
              try {
                localStorage.removeItem(backupKey(id))
              } catch {
                /* ignorado */
              }
              setBackup(null)
            }}
          >
            Descartar
          </Button>
        </div>
      )}

      <ErrorText>{error}</ErrorText>

      <div className="lg:hidden">
        <Segmented
          label="Área do editor"
          testId="skill-mobile-tabs"
          value={mobilePane}
          onChange={(v) => setMobilePane(v as MobilePane)}
          items={[
            { id: 'files', label: 'Arquivos', testId: 'mobile-tab-files' },
            { id: 'editor', label: 'Editor', testId: 'mobile-tab-editor' },
            { id: 'panel', label: 'Painel', testId: 'mobile-tab-panel' },
          ]}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[250px_minmax(0,1fr)_340px] xl:grid-cols-[260px_minmax(0,1fr)_380px]">
        <section
          aria-label="Arquivos"
          className={`${mobilePane === 'files' ? 'block' : 'hidden'} rounded-[24px] border border-[var(--border)] bg-[var(--surface)] p-3 lg:block`}
        >
          <FileTree
            files={filePaths}
            folders={folders}
            selected={activePath}
            dirty={dirtyPaths}
            problems={problemMap}
            readOnly={readOnly}
            onSelect={selectFile}
            onNewFile={(parent) => openPathDialog({ mode: 'new-file', kind: 'file', initial: parent ? `${parent}/` : '' })}
            onNewFolder={(parent) => openPathDialog({ mode: 'new-folder', kind: 'folder', initial: parent ? `${parent}/` : '' })}
            onRename={(path, kind) => openPathDialog({ mode: 'rename', kind, from: path, initial: path })}
            onDelete={(path, kind) => setDeleting({ path, kind })}
          />
          {!readOnly && (
            <Button variant="ghost" className="mt-3 w-full" data-testid="new-skill-btn" onClick={() => setNewSkillOpen(true)}>
              + Nova skill
            </Button>
          )}
        </section>

        <section
          aria-label="Editor"
          className={`${mobilePane === 'editor' ? 'block' : 'hidden'} min-w-0 space-y-3 lg:block`}
        >
          {activeFile ? (
            <>
              <div className="flex items-center justify-between gap-3">
                <h2 data-testid="active-file-name" className="truncate text-sm font-semibold text-[var(--text)]">
                  {activeFile.path}
                  {dirtyPaths.has(activeFile.path) && <span className="ml-1 text-[var(--brand)]">*</span>}
                </h2>
              </div>
              {isSkillMd && (
                <FrontmatterForm
                  content={activeFile.content}
                  tools={catalog}
                  readOnly={readOnly}
                  onChange={(next) => updateContent(activeFile.path, next)}
                />
              )}
              <MarkdownEditor
                path={activeFile.path}
                value={activeFile.content}
                readOnly={readOnly}
                onChange={(v) => updateContent(activeFile.path, v)}
              />
            </>
          ) : (
            <div className="rounded-[24px] border border-dashed border-[var(--border-strong)] p-8 text-center text-sm text-[var(--text-muted)]">
              Nenhum arquivo selecionado. Crie o arquivo AGENTE.md para começar.
            </div>
          )}
        </section>

        <section
          aria-label="Painel"
          className={`${mobilePane === 'panel' ? 'block' : 'hidden'} min-w-0 rounded-[24px] border border-[var(--border)] bg-[var(--surface)] p-4 lg:block`}
        >
          <Tabs label="Painel do agente" items={panelItems} value={panelTab} onChange={(v) => setPanelTab(v as PanelTab)} className="mb-4" />

          {panelTab === 'preview' && (
            <div data-testid="skill-preview" className="prose-sm max-h-[32rem] overflow-y-auto text-sm leading-6 text-[var(--text)] [&_a]:text-[var(--brand)] [&_code]:rounded [&_code]:bg-[var(--surface-soft)] [&_code]:px-1 [&_h1]:mb-2 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-lg [&_h2]:font-semibold [&_li]:ml-5 [&_li]:list-disc [&_ol>li]:list-decimal [&_p]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:bg-[var(--surface-soft)] [&_pre]:p-3">
              {activeFile ? (
                /\.md$/.test(activeFile.path) ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{stripFrontmatter(activeFile.content)}</ReactMarkdown>
                ) : (
                  <pre>{activeFile.content}</pre>
                )
              ) : (
                <p className="text-[var(--text-faint)]">Nada para mostrar.</p>
              )}
            </div>
          )}

          {panelTab === 'problems' && (
            <div data-testid="validation-panel" className="space-y-3">
              <p data-testid="validation-summary" aria-live="polite" className="text-sm text-[var(--text-muted)]">
                {validState === 'running'
                  ? 'Validando…'
                  : validState === 'failed'
                    ? 'Não foi possível validar.'
                    : validState === 'idle'
                      ? 'A validação roda automaticamente enquanto você edita.'
                      : problems.length === 0
                        ? ''
                        : `${errorCount} ${errorCount === 1 ? 'erro' : 'erros'}, ${warningCount} ${warningCount === 1 ? 'aviso' : 'avisos'}`}
              </p>
              {validationError && <ErrorText>{validationError}</ErrorText>}
              {validState === 'done' && problems.length === 0 && (
                <p data-testid="validation-ok" className="rounded-2xl bg-[var(--success-soft)] px-4 py-3 text-sm text-[var(--success)]">
                  Tudo certo: o pacote está pronto para publicar.
                </p>
              )}
              <ul className="space-y-2">
                {problems.map((p, i) => (
                  <li key={`${p.path}:${p.code}:${p.line ?? 0}:${i}`}>
                    <button
                      type="button"
                      data-testid="problem-item"
                      data-code={p.code}
                      data-severity={p.severity}
                      data-path={p.path}
                      onClick={() => p.path && filePaths.includes(p.path) && selectFile(p.path)}
                      className="w-full rounded-2xl border border-[var(--border)] bg-[var(--surface-solid)] px-3 py-2 text-left text-sm transition hover:border-[var(--brand)]"
                    >
                      <span
                        className={`mr-2 inline-block rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase ${
                          p.severity === 'error' ? 'bg-[var(--danger-soft)] text-[var(--danger)]' : 'bg-[var(--info-soft)] text-[var(--info)]'
                        }`}
                      >
                        {p.severity === 'error' ? 'Erro' : 'Aviso'}
                      </span>
                      <span className="font-medium text-[var(--text)]">{PROBLEM_TITLE[p.code] ?? p.code}</span>
                      <span className="mt-1 block text-[var(--text-muted)]">{p.message}</span>
                      {(p.path || p.line) && (
                        <span className="mt-1 block text-xs text-[var(--text-faint)]">
                          {p.path}
                          {p.line ? ` · linha ${p.line}` : ''}
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {panelTab === 'manifest' && (
            <div data-testid="manifest-panel" className="space-y-5">
              <div>
                <h3 className="text-sm font-semibold text-[var(--text)]">Ferramentas liberadas</h3>
                <p className="mt-1 text-xs text-[var(--text-faint)]">
                  O texto das skills nunca concede permissão: só as ferramentas marcadas aqui ficam disponíveis ao agente.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {catalog.map((t) => (
                    <Chip
                      key={t.id}
                      testId="manifest-tool"
                      selected={manifest.tools.includes(t.id)}
                      disabled={readOnly}
                      title={t.description}
                      onClick={() =>
                        setManifest((m) => ({
                          ...m,
                          tools: m.tools.includes(t.id) ? m.tools.filter((x) => x !== t.id) : [...m.tools, t.id],
                        }))
                      }
                    >
                      {t.label}
                      {t.sensitive && (
                        <span data-testid="tool-sensitive" className="rounded bg-[var(--danger-soft)] px-1 text-[10px] text-[var(--danger)]">
                          sensível
                        </span>
                      )}
                    </Chip>
                  ))}
                  {catalog.length === 0 && <span className="text-xs text-[var(--text-faint)]">catálogo indisponível</span>}
                </div>
              </div>
              <label className="flex items-start gap-3 text-sm text-[var(--text)]">
                <input
                  type="checkbox"
                  role="switch"
                  data-testid="manifest-env-toggle"
                  disabled={readOnly}
                  checked={manifest.environment.enabled}
                  onChange={(e) => setManifest((m) => ({ ...m, environment: { enabled: e.target.checked } }))}
                  className="mt-1 h-4 w-4 accent-[var(--brand)]"
                />
                <span>
                  Usar o ambiente do agente
                  <span className="block text-xs text-[var(--text-faint)]">
                    Permite ao agente trabalhar no servidor da empresa (instalar e publicar aplicações).
                  </span>
                </span>
              </label>
              <div className="grid gap-3 sm:grid-cols-2">
                <Input
                  label="Modelo (apelido)"
                  name="manifest-model"
                  data-testid="manifest-model"
                  disabled={readOnly}
                  value={manifest.model?.alias ?? ''}
                  placeholder="padrão da empresa"
                  onChange={(e) => setManifest((m) => ({ ...m, model: e.target.value ? { alias: e.target.value } : null }))}
                />
                <Input
                  label="Máx. de turnos por execução"
                  name="manifest-max-turns"
                  data-testid="manifest-max-turns"
                  type="number"
                  min={1}
                  max={200}
                  disabled={readOnly}
                  value={manifest.limits.max_turns ?? ''}
                  onChange={(e) =>
                    setManifest((m) => {
                      const { max_turns: _drop, ...rest } = m.limits
                      return { ...m, limits: e.target.value ? { ...rest, max_turns: Number(e.target.value) } : rest }
                    })
                  }
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Input
                  label="Tabelas que o agente pode alterar"
                  name="manifest-write-tables"
                  data-testid="manifest-write-tables"
                  disabled={readOnly}
                  value={(manifest.limits.write_tables ?? []).join(', ')}
                  placeholder="ex.: vendas, pedidos (vazio = nenhuma)"
                  onChange={(e) =>
                    setManifest((m) => {
                      const list = e.target.value.split(/[,\s]+/).filter(Boolean)
                      const { write_tables: _drop, ...rest } = m.limits
                      return { ...m, limits: list.length ? { ...rest, write_tables: list } : rest }
                    })
                  }
                />
                <Input
                  label="Endereços externos permitidos"
                  name="manifest-http-hosts"
                  data-testid="manifest-http-hosts"
                  disabled={readOnly}
                  value={(manifest.limits.http_allowed_hosts ?? []).join(', ')}
                  placeholder="ex.: api.minhaempresa.com.br"
                  onChange={(e) =>
                    setManifest((m) => {
                      const list = e.target.value.split(/[,\s]+/).filter(Boolean)
                      const { http_allowed_hosts: _drop, ...rest } = m.limits
                      return { ...m, limits: list.length ? { ...rest, http_allowed_hosts: list } : rest }
                    })
                  }
                />
              </div>
            </div>
          )}

          {panelTab === 'test' && (
            <TestBox templateId={id} hasActiveVersion={Boolean(activeVersionId)} />
          )}
        </section>
      </div>

      <VersionsCard
        versions={(versionsQ.data ?? []).map((v) => ({ id: v.id, number: v.number, created_at: v.created_at, notes: v.notes }))}
        activeId={activeVersionId}
        actionLabel="Restaurar"
        busy={restore.isPending}
        readOnly={readOnly}
        onAction={(vid) => setRestoring(vid)}
        extra={(v) =>
          readOnly ? null : (
            <Button variant="ghost" data-testid="version-load" onClick={() => setLoadFromVersion(v.id)}>
              Abrir no rascunho
            </Button>
          )
        }
      />

      <Modal
        open={pathDialog !== null}
        onClose={() => setPathDialog(null)}
        testId="path-dialog"
        title={
          pathDialog?.mode === 'new-file'
            ? 'Novo arquivo'
            : pathDialog?.mode === 'new-folder'
              ? 'Nova pasta'
              : pathDialog?.kind === 'folder'
                ? 'Renomear ou mover pasta'
                : 'Renomear ou mover arquivo'
        }
        description={
          pathDialog?.mode === 'new-folder' || (pathDialog?.mode === 'rename' && pathDialog.kind === 'folder')
            ? 'Use o caminho completo da pasta, por exemplo skills/triagem ou skills/triagem/references.'
            : 'Use o caminho completo, por exemplo skills/triagem/SKILL.md ou skills/triagem/references/precos.md.'
        }
      >
        <form onSubmit={submitPath} className="space-y-4" noValidate>
          <Input
            label="Caminho"
            name="path-input"
            data-testid="path-input"
            value={pathValue}
            error={pathError}
            onChange={(e) => setPathValue(e.target.value)}
          />
          <div className="flex justify-end gap-3">
            <Button type="button" variant="ghost" onClick={() => setPathDialog(null)}>
              Cancelar
            </Button>
            <Button type="submit" data-testid="path-confirm">
              Confirmar
            </Button>
          </div>
        </form>
      </Modal>

      <Modal open={newSkillOpen} onClose={() => setNewSkillOpen(false)} testId="new-skill-dialog" title="Nova skill" description="Cria skills/<nome>/SKILL.md com um modelo para preencher.">
        <form onSubmit={submitNewSkill} className="space-y-4" noValidate>
          <Input
            label="Nome da skill"
            hint="Minúsculas, números, ponto, hífen ou sublinhado."
            name="new-skill-name"
            data-testid="new-skill-name"
            value={newSkillName}
            error={newSkillError}
            onChange={(e) => setNewSkillName(e.target.value)}
          />
          <div className="flex justify-end gap-3">
            <Button type="button" variant="ghost" onClick={() => setNewSkillOpen(false)}>
              Cancelar
            </Button>
            <Button type="submit" data-testid="new-skill-confirm">
              Criar skill
            </Button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        open={deleting !== null}
        title={deleting?.kind === 'folder' ? 'Excluir pasta' : 'Excluir arquivo'}
        message={
          deleting?.kind === 'folder'
            ? `Excluir a pasta ${deleting.path} e tudo que está dentro dela? A mudança só vale depois de salvar o rascunho.`
            : `Excluir ${deleting?.path}? A mudança só vale depois de salvar o rascunho.`
        }
        confirmLabel="Excluir"
        danger
        onClose={() => setDeleting(null)}
        onConfirm={confirmDelete}
        testId="delete-dialog"
      />

      <ConfirmDialog
        open={restoring !== null}
        title="Restaurar versão"
        message="Esta versão volta a ser a que está em produção e passa a valer no chat agora."
        confirmLabel="Restaurar"
        busy={restore.isPending}
        onClose={() => setRestoring(null)}
        onConfirm={() => {
          const vid = restoring
          setRestoring(null)
          if (vid) restore.mutate(vid)
        }}
        testId="restore-dialog"
      />

      <ConfirmDialog
        open={loadFromVersion !== null}
        title="Abrir versão no rascunho"
        message="Os arquivos do rascunho atual serão substituídos pelos desta versão. Você ainda precisa salvar para guardar."
        confirmLabel="Abrir no rascunho"
        onClose={() => setLoadFromVersion(null)}
        onConfirm={() => {
          const vid = loadFromVersion
          setLoadFromVersion(null)
          if (vid) void loadVersionIntoDraft(vid)
        }}
        testId="load-version-dialog"
      />
    </div>
  )
}

/** Caixa "Testar mensagem": conversa com a versão em produção. */
function TestBox({ templateId, hasActiveVersion }: { templateId: string; hasActiveVersion: boolean }) {
  const [text, setText] = useState('')
  const [output, setOutput] = useState('')
  const [steps, setSteps] = useState<StepEvent[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => () => abortRef.current?.abort(), [])

  async function run(e: FormEvent) {
    e.preventDefault()
    const message = text.trim()
    if (!message || busy) return
    setBusy(true)
    setOutput('')
    setSteps([])
    setError('')
    const controller = new AbortController()
    abortRef.current = controller
    let acc = ''
    await sendMessage(
      message,
      null,
      {
        onToken: (t) => {
          acc += t
          setOutput(acc)
        },
        onStep: (s) =>
          setSteps((list) => [
            ...list.filter((x) => x.kind !== s.kind || (s.kind !== 'queue' && x.label !== s.label)),
            s,
          ]),
        onDone: (full) => setOutput(full || acc),
        onError: (detail) => setError(detail),
      },
      templateId,
      undefined,
      controller.signal,
    )
    setBusy(false)
  }

  return (
    <form onSubmit={run} data-testid="test-panel" className="space-y-3">
      <p className="text-xs text-[var(--text-faint)]">
        O teste usa a versão em produção, não o rascunho. Publique para testar as mudanças.
      </p>
      {!hasActiveVersion && (
        <p data-testid="test-disabled-note" className="rounded-2xl bg-[var(--surface-soft)] px-3 py-2 text-sm text-[var(--text-muted)]">
          Publique uma versão para poder testar.
        </p>
      )}
      <label className="block">
        <span className="sr-only">Mensagem de teste</span>
        <textarea
          name="test-message"
          data-testid="test-message-input"
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Escreva uma mensagem para o agente"
          className="w-full rounded-2xl border border-[var(--border)] bg-[var(--surface-solid)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--brand)]"
        />
      </label>
      <Button type="submit" disabled={!hasActiveVersion || busy || !text.trim()} data-testid="test-message-send">
        {busy ? 'Enviando…' : 'Enviar'}
      </Button>
      {steps.length > 0 && (
        <ul data-testid="test-steps" className="space-y-1 text-xs text-[var(--text-muted)]">
          {steps.map((s, i) => (
            <li key={i}>{stepText(s)}</li>
          ))}
        </ul>
      )}
      {error && <ErrorText>{error}</ErrorText>}
      {output && (
        <div data-testid="test-output" className="rounded-2xl border border-[var(--border)] bg-[var(--surface-solid)] p-3 text-sm leading-6 text-[var(--text)]">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{output}</ReactMarkdown>
        </div>
      )}
    </form>
  )
}
