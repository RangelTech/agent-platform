// Guarda de vocabulário: a interface nunca cita os nomes internos do runtime.
// "hermes" só é permitido na tela legada congelada (produto Hermes).
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

const root = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
const dirs = ['src', 'e2e', 'e2e-mock', 'index.html', 'public']
const ALLOW_HERMES = new Set(['src/App.tsx', 'src/components/SurfaceSwitcher.tsx', 'src/pages/HermesAgente.tsx', 'src/pages/Chat.tsx'])
const ALLOW_ALL = new Set(['e2e/tenant-journey.spec.ts', 'e2e-mock/vocabulario.spec.ts'])
const bad = []

function walk(p) {
  const st = statSync(p)
  if (st.isDirectory()) return readdirSync(p).flatMap((n) => walk(join(p, n)))
  return /\.(tsx?|html|css|json|md)$/.test(p) ? [p] : []
}

for (const d of dirs) {
  let files = []
  try {
    files = walk(join(root, d))
  } catch {
    continue
  }
  for (const f of files) {
    const rel = relative(root, f).split(sep).join('/')
    if (ALLOW_ALL.has(rel)) continue
    readFileSync(f, 'utf8').split('\n').forEach((line, i) => {
      if (/harness|kernel/i.test(line)) bad.push(`${rel}:${i + 1}: ${line.trim()}`)
      if (/hermes/i.test(line) && !ALLOW_HERMES.has(rel)) bad.push(`${rel}:${i + 1}: ${line.trim()}`)
    })
  }
}
if (bad.length) {
  console.error('Vocabulário proibido encontrado:\n' + bad.join('\n'))
  process.exit(1)
}
console.log('Vocabulário OK')
