-- 0058: usuario removido da empresa no Chatwoot (member_revoked).
-- revoked_at = momento da remocao no Rails. Contextos assinados emitidos ANTES dele sao
-- recusados (403); um contexto emitido DEPOIS (pessoa readicionada) reativa a projecao.
ALTER TABLE users ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;
