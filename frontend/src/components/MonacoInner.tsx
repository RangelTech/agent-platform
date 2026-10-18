import Editor, { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor/editor/editor.api'
import 'monaco-editor/languages/definitions/markdown/register'
import 'monaco-editor/languages/definitions/python/register'
import 'monaco-editor/languages/definitions/shell/register'
import 'monaco-editor/languages/definitions/javascript/register'
import 'monaco-editor/languages/definitions/yaml/register'
import EditorWorker from 'monaco-editor/editor/editor.worker?worker'

// Monaco empacotado localmente (sem CDN): o editor funciona em rede restrita e
// nos testes automatizados. Só o worker base é necessário para markdown.
;(self as unknown as { MonacoEnvironment: unknown }).MonacoEnvironment = {
  getWorker: () => new EditorWorker(),
}
loader.config({ monaco })

export interface MonacoInnerProps {
  value: string
  language: string
  readOnly?: boolean
  ariaLabel: string
  modelPath: string
  height: string
  onChange: (value: string) => void
}

export default function MonacoInner({ value, language, readOnly, ariaLabel, modelPath, height, onChange }: MonacoInnerProps) {
  const theme = document.documentElement.dataset.theme === 'light' ? 'vs' : 'vs-dark'
  return (
    <Editor
      height={height}
      loading={<span className="text-sm text-[var(--text-muted)]">Carregando editor…</span>}
      path={`inmemory://model/${modelPath}`}
      language={language}
      theme={theme}
      value={value}
      onChange={(v) => onChange(v ?? '')}
      options={{
        ariaLabel,
        readOnly,
        automaticLayout: true,
        fontSize: 14,
        minimap: { enabled: false },
        wordWrap: 'on',
        padding: { top: 12, bottom: 12 },
        scrollBeyondLastLine: false,
        tabSize: 2,
        lineNumbers: 'on',
      }}
    />
  )
}
