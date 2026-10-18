/**
 * Edição mínima do cabeçalho (frontmatter YAML) de uma skill.
 *
 * O formulário só gerencia `name`, `description`, `version` e `allowed_tools`;
 * qualquer outra linha do cabeçalho é preservada como está. Não é um parser
 * YAML completo: cobre o formato simples que as skills usam (chave: valor e
 * listas em linha `[a, b]`). Quando o cabeçalho foge disso, `parsed.ok` fica
 * falso e o formulário avisa em vez de reescrever.
 */

export interface Frontmatter {
  hasBlock: boolean
  ok: boolean
  name: string
  description: string
  version: string
  tools: string[]
}

interface Split {
  hasBlock: boolean
  closed: boolean
  lines: string[]
  body: string
}

function splitDoc(text: string): Split {
  const src = text.replace(/\r\n/g, '\n')
  if (!src.startsWith('---\n')) return { hasBlock: false, closed: false, lines: [], body: src }
  const end = src.indexOf('\n---', 4)
  if (end < 0) return { hasBlock: true, closed: false, lines: src.slice(4).split('\n'), body: '' }
  const head = src.slice(4, end)
  let body = src.slice(end + 4)
  if (body.startsWith('\n')) body = body.slice(1)
  return { hasBlock: true, closed: true, lines: head.length ? head.split('\n') : [], body }
}

function unquote(raw: string): string {
  const v = raw.trim()
  if (v.length >= 2 && ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'")))) {
    return v.slice(1, -1).replace(/\\"/g, '"')
  }
  return v
}

function quoteIfNeeded(v: string): string {
  if (v === '') return '""'
  if (/^[\s>|&*!%@`#'"-]|[:#]\s|\s$|^\[|^\{/.test(v) || /: /.test(v)) return `"${v.replace(/"/g, '\\"')}"`
  return v
}

function keyOf(line: string): string | null {
  const m = /^([A-Za-z_][\w-]*)\s*:/.exec(line)
  return m ? m[1] : null
}

export function parseFrontmatter(text: string): Frontmatter {
  const s = splitDoc(text)
  const out: Frontmatter = { hasBlock: s.hasBlock, ok: !s.hasBlock || s.closed, name: '', description: '', version: '', tools: [] }
  for (const line of s.lines) {
    const key = keyOf(line)
    if (!key) continue
    const raw = line.slice(line.indexOf(':') + 1)
    if (key === 'name') out.name = unquote(raw)
    else if (key === 'description') out.description = unquote(raw)
    else if (key === 'version') out.version = unquote(raw)
    else if (key === 'allowed_tools') {
      const t = raw.trim()
      if (t.startsWith('[') && t.endsWith(']')) {
        out.tools = t
          .slice(1, -1)
          .split(',')
          .map((x) => unquote(x))
          .filter(Boolean)
      }
    }
  }
  return out
}

export type FrontmatterPatch = Partial<Pick<Frontmatter, 'name' | 'description' | 'version' | 'tools'>>

/** Reescreve só as chaves gerenciadas; devolve o texto completo do arquivo. */
export function patchFrontmatter(text: string, patch: FrontmatterPatch): string {
  const s = splitDoc(text)
  const lines = s.hasBlock && s.closed ? [...s.lines] : []
  const body = s.hasBlock && !s.closed ? text.replace(/\r\n/g, '\n') : s.body
  const render = (key: string, value: string | string[]) =>
    key === 'allowed_tools'
      ? `allowed_tools: [${(value as string[]).join(', ')}]`
      : `${key}: ${quoteIfNeeded(value as string)}`
  const entries: [string, string | string[] | undefined][] = [
    ['name', patch.name],
    ['description', patch.description],
    ['version', patch.version],
    ['allowed_tools', patch.tools],
  ]
  for (const [key, value] of entries) {
    if (value === undefined) continue
    const idx = lines.findIndex((l) => keyOf(l) === key)
    const empty = Array.isArray(value) ? value.length === 0 : value === ''
    if (idx >= 0) {
      if (empty && key !== 'name' && key !== 'description') lines.splice(idx, 1)
      else lines[idx] = render(key, value)
    } else if (!empty || key === 'name' || key === 'description') {
      // Mantém a ordem canônica: name, description, version, allowed_tools.
      lines.push(render(key, value))
    }
  }
  return `---\n${lines.join('\n')}\n---\n${body}`
}

/** Corpo sem o cabeçalho, para a prévia renderizada. */
export function stripFrontmatter(text: string): string {
  const s = splitDoc(text)
  return s.hasBlock && s.closed ? s.body : text.replace(/\r\n/g, '\n')
}

export function newSkillTemplate(name: string): string {
  return `---\nname: ${name}\ndescription: Quando usar esta skill (uma frase).\n---\n# ${name}\n\nDescreva o passo a passo que o agente deve seguir.\n`
}

export const AGENT_MD_TEMPLATE = `# Agente\n\nDescreva a persona, o objetivo, o tom e as regras do agente.\n`
