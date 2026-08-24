import { useEffect, useState } from 'react'

/**
 * Página de callback do OAuth de assinatura (Claude/Codex/Antigravity/Gemini
 * CLI/Cline) — produto-08. Fica FORA do Gate autenticado de propósito (igual
 * as páginas legais): o provedor redireciona pra cá antes de qualquer sessão
 * nossa existir nessa aba.
 *
 * Portado do 9Router (`src/app/callback/page.js`), mesmo desenho: repassa o
 * código pra aba que abriu o popup via postMessage (rápido, fecha sozinho) e
 * cai pra exibir o código pra colar na mão se o repasse falhar por algum
 * motivo (bloqueador de popup, aba que não é opener direto, etc.).
 */
export default function OAuthCallback() {
  const [status, setStatus] = useState<'processando' | 'sucesso' | 'manual'>('processando')

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const code = params.get('code')
    const state = params.get('state')
    const error = params.get('error')
    const dados = { code, state, error, url: window.location.href }

    let repassado = false
    if (window.opener) {
      try {
        window.opener.postMessage({ type: 'oauth_callback', dados }, window.location.origin)
        repassado = true
      } catch {
        // ignorado -- cai pro modo manual abaixo
      }
    }
    try {
      localStorage.setItem('oauth_callback', JSON.stringify({ ...dados, timestamp: Date.now() }))
      repassado = true
    } catch {
      // localStorage pode estar bloqueado (aba anônima restrita) -- não é fatal
    }

    if (!code && !error) {
      setStatus('manual')
      return
    }
    if (!repassado) {
      setStatus('manual')
      return
    }
    setStatus('sucesso')
    const t = setTimeout(() => window.close(), 1500)
    return () => clearTimeout(t)
  }, [])

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg)] px-6 text-center">
      <div className="max-w-md">
        {status === 'processando' && (
          <p className="text-[var(--text-muted)]">Processando autorização…</p>
        )}
        {status === 'sucesso' && (
          <>
            <p className="text-[var(--text)]">Autorização concluída.</p>
            <p className="mt-1 text-sm text-[var(--text-muted)]">Esta aba fecha sozinha…</p>
          </>
        )}
        {status === 'manual' && (
          <>
            <p className="text-[var(--text)]">Copie esta URL e cole na tela onde você iniciou a conexão.</p>
            <div className="mt-4 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3 text-left">
              <code className="break-all text-xs">{window.location.href}</code>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
