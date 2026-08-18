import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ArtifactRef } from './ArtifactCard'

export type ArtifactLinkHandler = (artifact: ArtifactRef) => void

const ARTIFACT_SCHEME = 'artifact:'

/**
 * Renderer de markdown das mensagens do assistente (ver `ux-03-markdown-no-chat.md`).
 *
 * Só é usado pra `role !== 'user'` — a mensagem do usuário continua texto cru,
 * porque ela não escreve markdown de propósito.
 *
 * Links com esquema customizado `artifact:<artifact_id>` (ver
 * `ux-05-link-inline-para-artefato.md`) não viram `<a href="artifact:...">`
 * morto: o clique é interceptado, resolve o `artifact_id` contra a lista de
 * artefatos da sessão (pra saber o `kind`) e delega pra `onArtifactLink`, que
 * decide o que fazer (baixar arquivo, ou rolar/destacar no painel) conforme o
 * tipo do artefato.
 */
export function MarkdownMessage({
  content,
  artifacts = [],
  onArtifactLink,
  className = '',
}: {
  content: string
  artifacts?: ArtifactRef[]
  onArtifactLink?: ArtifactLinkHandler
  className?: string
}) {
  const components: Components = {
    a({ href, children, node: _node, ...rest }) {
      if (href?.startsWith(ARTIFACT_SCHEME)) {
        const artifactId = href.slice(ARTIFACT_SCHEME.length)
        return (
          <a
            href="#"
            onClick={(e) => {
              e.preventDefault()
              const artifact = artifacts.find((a) => a.artifact_id === artifactId)
              if (artifact && onArtifactLink) {
                onArtifactLink(artifact)
              } else {
                // Artefato ainda não conhecido na sessão (ou sem handler
                // configurado) — pelo menos não fica um link morto sem
                // nenhum feedback ao clicar.
                console.warn('Link de artefato não resolvido:', artifactId)
              }
            }}
            className="cursor-pointer font-medium text-[var(--brand)] underline decoration-dotted underline-offset-2"
          >
            {children}
          </a>
        )
      }
      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="font-medium text-[var(--brand)] underline underline-offset-2"
          {...rest}
        >
          {children}
        </a>
      )
    },
    table({ children }) {
      return (
        <div className="my-2 overflow-x-auto rounded-xl border border-[var(--border)]">
          <table className="w-full min-w-max border-collapse text-sm">{children}</table>
        </div>
      )
    },
    thead({ children }) {
      return <thead className="bg-[var(--surface-soft)]">{children}</thead>
    },
    th({ children }) {
      return (
        <th className="border-b border-[var(--border)] px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
          {children}
        </th>
      )
    },
    td({ children }) {
      return <td className="border-b border-[var(--border)] px-3 py-2 align-top">{children}</td>
    },
    h1({ children }) {
      return <h1 className="mb-2 mt-3 text-lg font-semibold first:mt-0">{children}</h1>
    },
    h2({ children }) {
      return <h2 className="mb-2 mt-3 text-base font-semibold first:mt-0">{children}</h2>
    },
    h3({ children }) {
      return <h3 className="mb-1 mt-3 text-sm font-semibold first:mt-0">{children}</h3>
    },
    p({ children }) {
      return <p className="mb-2 last:mb-0">{children}</p>
    },
    ul({ children }) {
      return <ul className="mb-2 list-disc space-y-1 pl-5 last:mb-0">{children}</ul>
    },
    ol({ children }) {
      return <ol className="mb-2 list-decimal space-y-1 pl-5 last:mb-0">{children}</ol>
    },
    hr() {
      return <hr className="my-3 border-[var(--border)]" />
    },
    strong({ children }) {
      return <strong className="font-semibold">{children}</strong>
    },
    code({ children }) {
      return (
        <code className="rounded bg-[var(--surface-soft)] px-1 py-0.5 text-[0.9em]">{children}</code>
      )
    },
  }

  return (
    <div className={`markdown-body ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
