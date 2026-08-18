import { useEffect, useState } from 'react'
import { getToken } from '../lib/api'

export interface ArtifactRef {
  artifact_id: string
  kind: string
  title: string
}

/**
 * Imagem gerada na conversa — hoje, o QR Code do PIX.
 *
 * O download exige o cabeçalho de autenticação, então não dá para apontar o
 * `src` direto para a rota: a imagem é buscada e vira uma URL de blob.
 */
function ImageView({ artifactId, title }: { artifactId: string; title: string }) {
  const [src, setSrc] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    let url = ''
    async function carregar() {
      try {
        const token = getToken()
        const res = await fetch(`/api/artifacts/${artifactId}/download`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        })
        if (!res.ok) throw new Error('Falha ao carregar a imagem')
        const blob = await res.blob()
        if (cancelled) return
        url = URL.createObjectURL(blob)
        setSrc(url)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Falha ao carregar')
      }
    }
    carregar()
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [artifactId])

  if (error) return <p className="text-sm text-[var(--danger)]">{error}</p>
  if (!src) return <div className="h-48 w-48 animate-pulse rounded-xl bg-[var(--surface-soft)]" />
  return <img src={src} alt={title} className="h-auto w-48 max-w-full rounded-xl bg-white p-2" />
}

/**
 * Baixa um artefato do tipo arquivo/imagem — exportada para ser reusada pelo
 * link inline `artifact:` que o markdown do chat intercepta (ver
 * `MarkdownMessage.tsx`), em vez de duplicar a lógica de fetch+blob lá.
 */
export async function downloadArtifact(artifactId: string, title: string) {
  const token = getToken()
  const res = await fetch(`/api/artifacts/${artifactId}/download`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) return
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = title || 'arquivo'
  link.click()
  URL.revokeObjectURL(url)
}

export function ArtifactCard({ artifact, highlighted = false }: { artifact: ArtifactRef; highlighted?: boolean }) {
  // Destaque temporário quando o usuário clica num link inline `artifact:`
  // na mensagem do assistente — mesma borda em todos os tipos de card.
  const highlightClass = highlighted
    ? 'ring-2 ring-[var(--brand)] shadow-[0_0_0_6px_color-mix(in_srgb,var(--brand)_20%,transparent)] transition-shadow'
    : 'transition-shadow'
  const domId = `artifact-${artifact.artifact_id}`

  if (artifact.kind === 'image') {
    return (
      <div
        id={domId}
        className={`rounded-[20px] border border-[var(--border)] bg-[var(--surface-solid)] p-3 ${highlightClass}`}
        data-testid="artifact-image"
      >
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">
          {artifact.title}
        </p>
        <ImageView artifactId={artifact.artifact_id} title={artifact.title} />
        <button
          onClick={() => downloadArtifact(artifact.artifact_id, artifact.title)}
          className="mt-2 text-xs text-[var(--text-muted)] underline"
        >
          Baixar imagem
        </button>
      </div>
    )
  }
  if (artifact.kind === 'file') {
    return (
      <button
        id={domId}
        onClick={() => downloadArtifact(artifact.artifact_id, artifact.title)}
        data-testid="artifact-file"
        className={`flex min-h-11 items-center gap-2 rounded-full border border-[var(--border)] bg-[var(--surface-solid)] px-4 py-2 text-sm text-[var(--text)] transition hover:border-[var(--brand)] ${highlightClass}`}
      >
        <span aria-hidden>⬇</span> {artifact.title}
      </button>
    )
  }
  return (
    <span
      id={domId}
      data-testid="artifact-dataset"
      className={`inline-flex items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--surface-solid)] px-3 py-1 text-xs text-[var(--text-muted)] ${highlightClass}`}
    >
      dataset · {artifact.title}
    </span>
  )
}
