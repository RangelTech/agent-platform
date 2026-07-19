import { useEffect, useRef, useState } from 'react'
import { api, getToken } from '../lib/api'

export interface ArtifactRef {
  artifact_id: string
  kind: string
  title: string
}

function ChartView({ artifactId }: { artifactId: string }) {
  const container = useRef<HTMLDivElement>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    async function render() {
      try {
        const figure = await api<{ data: unknown[]; layout: Record<string, unknown> }>(
          `/artifacts/${artifactId}/payload`,
        )
        if (cancelled || !container.current) return
        // Plotly is ~1MB gzipped — loaded lazily only when a chart appears.
        const Plotly = (await import('plotly.js-dist-min')).default
        if (cancelled || !container.current) return
        await Plotly.newPlot(
          container.current,
          figure.data,
          { ...figure.layout, autosize: true, paper_bgcolor: 'transparent' },
          { responsive: true, displaylogo: false },
        )
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Falha ao renderizar')
      }
    }
    render()
    return () => {
      cancelled = true
    }
  }, [artifactId])

  if (error) return <p className="text-sm text-rose-400">{error}</p>
  return <div ref={container} data-testid="chart-container" className="min-h-[420px] w-full" />
}

async function downloadArtifact(artifactId: string, title: string) {
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

export function ArtifactCard({ artifact }: { artifact: ArtifactRef }) {
  if (artifact.kind === 'chart') {
    return (
      <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3" data-testid="artifact-chart">
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
          {artifact.title}
        </p>
        <ChartView artifactId={artifact.artifact_id} />
      </div>
    )
  }
  if (artifact.kind === 'file') {
    return (
      <button
        onClick={() => downloadArtifact(artifact.artifact_id, artifact.title)}
        data-testid="artifact-file"
        className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-4 py-2 text-sm text-slate-200 transition hover:border-indigo-500"
      >
        <span aria-hidden>⬇</span> {artifact.title}
      </button>
    )
  }
  return (
    <span
      data-testid="artifact-dataset"
      className="inline-flex items-center gap-1 rounded-full border border-slate-700 bg-slate-900 px-3 py-1 text-xs text-slate-400"
    >
      dataset · {artifact.title}
    </span>
  )
}
