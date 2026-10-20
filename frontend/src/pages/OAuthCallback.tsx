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
  const [status, setStatus] = useState<'processando' | 'sucesso' | 'conectado' | 'manual'>('processando')

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const code = params.get('code')
    const state = params.get('state')
    const error = params.get('error')
    const dados = { code, state, error, url: window.location.href }
    let cancelado = false
    let timer: ReturnType<typeof setTimeout> | undefined

    function repassar() {
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
      if ((!code && !error) || !repassado) {
        setStatus('manual')
        return
      }
      setStatus('sucesso')
      timer = setTimeout(() => window.close(), 1500)
    }

    if (code && state) {
      // Conexão iniciada pela tela de credenciais do Luar: o motor guardou o state e fecha a
      // conexão sozinho. Falha (state desconhecido = fluxo antigo do painel do motor) cai no repasse.
      fetch('/api/oauth/finalizar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ state, code }),
      })
        .then((r) => r.ok)
        .catch(() => false)
        .then((feito) => {
          if (cancelado) return
          if (feito) {
            setStatus('conectado')
            timer = setTimeout(() => window.close(), 2500)
          } else {
            repassar()
          }
        })
    } else {
      repassar()
    }
    return () => {
      cancelado = true
      if (timer) clearTimeout(timer)
    }
  }, [])

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg)] px-6 text-center">
      <div className="max-w-md">
        {status === 'processando' && (
          <p className="text-[var(--text-muted)]">Processando autorização…</p>
        )}
        {status === 'conectado' && (
          <>
            <p className="text-[var(--text)]">Conta conectada.</p>
            <p className="mt-1 text-sm text-[var(--text-muted)]">
              Pode fechar esta aba e voltar para a tela de credenciais.
            </p>
          </>
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
