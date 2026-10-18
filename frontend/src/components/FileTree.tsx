import { ChevronDown, ChevronRight, FileText, Folder, FolderPlus, Pencil, Plus, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'

interface Node {
  name: string
  path: string
  kind: 'file' | 'folder'
  children: Node[]
}

/** Monta a árvore a partir de caminhos de arquivo e pastas vazias explícitas. */
export function buildTree(files: string[], folders: string[]): Node[] {
  const root: Node = { name: '', path: '', kind: 'folder', children: [] }
  const ensureFolder = (parts: string[]): Node => {
    let cur = root
    parts.forEach((part, i) => {
      const path = parts.slice(0, i + 1).join('/')
      let next = cur.children.find((c) => c.kind === 'folder' && c.name === part)
      if (!next) {
        next = { name: part, path, kind: 'folder', children: [] }
        cur.children.push(next)
      }
      cur = next
    })
    return cur
  }
  for (const folder of folders) if (folder) ensureFolder(folder.split('/'))
  for (const file of files) {
    const parts = file.split('/')
    const parent = ensureFolder(parts.slice(0, -1))
    parent.children.push({ name: parts[parts.length - 1], path: file, kind: 'file', children: [] })
  }
  const sort = (nodes: Node[], top: boolean) => {
    nodes.sort((a, b) => {
      if (top && a.path === 'AGENTE.md') return -1
      if (top && b.path === 'AGENTE.md') return 1
      if (a.kind !== b.kind) return a.kind === 'folder' ? -1 : 1
      return a.name.localeCompare(b.name, 'pt-BR')
    })
    nodes.forEach((n) => sort(n.children, false))
  }
  sort(root.children, true)
  return root.children
}

export interface FileTreeProps {
  files: string[]
  folders: string[]
  selected: string | null
  dirty: Set<string>
  /** path -> pior severidade de problema naquele arquivo */
  problems: Map<string, 'error' | 'warning'>
  readOnly?: boolean
  onSelect: (path: string) => void
  onNewFile: (parentFolder: string) => void
  onNewFolder: (parentFolder: string) => void
  onRename: (path: string, kind: 'file' | 'folder') => void
  onDelete: (path: string, kind: 'file' | 'folder') => void
}

const iconBtn =
  'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[var(--text-faint)] transition hover:bg-[var(--surface-soft)] hover:text-[var(--text)] focus-visible:text-[var(--text)]'

export function FileTree(props: FileTreeProps) {
  const { files, folders, selected, dirty, problems, readOnly } = props
  const tree = useMemo(() => buildTree(files, folders), [files, folders])
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  function toggle(path: string) {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  function renderNode(node: Node, depth: number) {
    const pad = { paddingLeft: `${depth * 14 + 8}px` }
    if (node.kind === 'folder') {
      const open = !collapsed.has(node.path)
      return (
        <li key={`d:${node.path}`}>
          <div
            data-testid="skill-folder-item"
            data-path={node.path}
            style={pad}
            className="group flex items-center gap-1 rounded-xl py-0.5 pr-1 hover:bg-[var(--surface-soft)]"
          >
            <button
              type="button"
              onClick={() => toggle(node.path)}
              aria-expanded={open}
              className="flex min-h-9 min-w-0 flex-1 items-center gap-1.5 text-left text-sm font-medium text-[var(--text)]"
            >
              {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
              <Folder size={15} aria-hidden="true" className="text-[var(--text-faint)]" />
              <span className="truncate">{node.name}</span>
            </button>
            {!readOnly && (
              <>
                <button
                  type="button"
                  className={iconBtn}
                  aria-label={`Novo arquivo em ${node.path}`}
                  title="Novo arquivo nesta pasta"
                  data-testid="folder-new-file"
                  onClick={() => props.onNewFile(node.path)}
                >
                  <Plus size={14} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className={iconBtn}
                  aria-label={`Renomear ou mover a pasta ${node.path}`}
                  title="Renomear ou mover pasta"
                  data-testid="folder-rename"
                  onClick={() => props.onRename(node.path, 'folder')}
                >
                  <Pencil size={14} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className={iconBtn}
                  aria-label={`Excluir a pasta ${node.path}`}
                  title="Excluir pasta"
                  data-testid="folder-delete"
                  onClick={() => props.onDelete(node.path, 'folder')}
                >
                  <Trash2 size={14} aria-hidden="true" />
                </button>
              </>
            )}
          </div>
          {open && <ul role="group">{node.children.map((c) => renderNode(c, depth + 1))}</ul>}
        </li>
      )
    }
    const isSel = selected === node.path
    const sev = problems.get(node.path)
    const isDirty = dirty.has(node.path)
    return (
      <li key={`f:${node.path}`}>
        <div
          style={pad}
          className={`group flex items-center gap-1 rounded-xl py-0.5 pr-1 ${
            isSel ? 'bg-[var(--brand-soft)]' : 'hover:bg-[var(--surface-soft)]'
          }`}
        >
          <button
            type="button"
            data-testid="skill-file-item"
            data-path={node.path}
            data-dirty={isDirty ? 'true' : 'false'}
            aria-current={isSel ? 'true' : undefined}
            onClick={() => props.onSelect(node.path)}
            className="flex min-h-9 min-w-0 flex-1 items-center gap-1.5 text-left text-sm text-[var(--text)]"
          >
            <FileText size={15} aria-hidden="true" className="shrink-0 text-[var(--text-faint)]" />
            <span className="truncate">{node.name}</span>
            {isDirty && (
              <span aria-label="alterações não salvas" title="Alterações não salvas" className="text-[var(--brand)]">
                *
              </span>
            )}
            {sev && (
              <span
                data-testid="file-problem-dot"
                data-severity={sev}
                aria-label={sev === 'error' ? 'com erros' : 'com avisos'}
                title={sev === 'error' ? 'Este arquivo tem erros' : 'Este arquivo tem avisos'}
                className={`ml-auto h-2 w-2 shrink-0 rounded-full ${sev === 'error' ? 'bg-[var(--danger)]' : 'bg-[var(--info)]'}`}
              />
            )}
          </button>
          {!readOnly && (
            <>
              <button
                type="button"
                className={iconBtn}
                aria-label={`Renomear ou mover ${node.path}`}
                title="Renomear ou mover"
                data-testid="file-rename"
                onClick={() => props.onRename(node.path, 'file')}
              >
                <Pencil size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                className={iconBtn}
                aria-label={`Excluir ${node.path}`}
                title="Excluir arquivo"
                data-testid="file-delete"
                onClick={() => props.onDelete(node.path, 'file')}
              >
                <Trash2 size={14} aria-hidden="true" />
              </button>
            </>
          )}
        </div>
      </li>
    )
  }

  return (
    <div data-testid="skill-file-tree" className="flex min-h-0 flex-col gap-3">
      <ul role="tree" aria-label="Arquivos do agente" className="min-h-0 flex-1 overflow-y-auto">
        {tree.map((n) => renderNode(n, 0))}
        {tree.length === 0 && <li className="px-2 py-3 text-sm text-[var(--text-faint)]">Nenhum arquivo.</li>}
      </ul>
      {!readOnly && (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            data-testid="tree-new-file"
            onClick={() => props.onNewFile('')}
            className="inline-flex min-h-10 items-center gap-1.5 rounded-full border border-[var(--border)] px-3 text-sm text-[var(--text)] transition hover:border-[var(--brand)] hover:bg-[var(--brand-soft)]"
          >
            <Plus size={14} aria-hidden="true" /> Arquivo
          </button>
          <button
            type="button"
            data-testid="tree-new-folder"
            onClick={() => props.onNewFolder('')}
            className="inline-flex min-h-10 items-center gap-1.5 rounded-full border border-[var(--border)] px-3 text-sm text-[var(--text)] transition hover:border-[var(--brand)] hover:bg-[var(--brand-soft)]"
          >
            <FolderPlus size={14} aria-hidden="true" /> Pasta
          </button>
        </div>
      )}
    </div>
  )
}
