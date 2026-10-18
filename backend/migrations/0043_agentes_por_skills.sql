-- 0043: agentes por skills (spec 04) -- tipo do agente, kernel do chat,
-- manifesto da versao, flag por empresa e registro de runs.
--
-- agent_kind: 'structured' (supervisor + especialistas, fluxo de sempre) ou
-- 'skills' (pacote de arquivos Markdown). Imutavel depois de criado
-- (garantido pela API; nenhuma rota altera a coluna).
-- Esta migracao nao mexe em 0041/0042 (ecossistema congelado).

ALTER TABLE templates
    ADD COLUMN IF NOT EXISTS agent_kind TEXT NOT NULL DEFAULT 'structured'
        CHECK (agent_kind IN ('structured', 'skills'));

-- Kernel que atende a conversa, fixado no 1o envio ('llm' = estruturado,
-- 'harness' = skills). Conversas antigas ja nasceram no fluxo estruturado.
ALTER TABLE chats ADD COLUMN IF NOT EXISTS kernel TEXT
    CHECK (kernel IS NULL OR kernel IN ('llm', 'harness'));
UPDATE chats SET kernel = 'llm' WHERE kernel IS NULL;

-- Versoes 'skills' gravam supervisor_prompt = '' (a coluna segue NOT NULL para
-- nao mudar o contrato do fluxo estruturado); o manifesto vive aqui.
ALTER TABLE template_versions ADD COLUMN IF NOT EXISTS manifest JSONB;

-- Liga/desliga agentes por skills por empresa (so o master altera).
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS harness_enabled BOOLEAN NOT NULL DEFAULT false;

-- Registro de execucoes (runs) de chat. Serve a: cancelamento (dono da run),
-- validacao das rotas internas (tenant/usuario/chat vem daqui, nunca do corpo),
-- persistencia unica da resposta e historico de "sessao nova".
CREATE TABLE IF NOT EXISTS chat_runs (
    run_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    user_id UUID REFERENCES users (id) ON DELETE SET NULL,
    chat_id TEXT NOT NULL,
    template_id UUID,
    version_id UUID,
    kernel TEXT NOT NULL CHECK (kernel IN ('llm', 'harness')),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'cancelling', 'done', 'error', 'cancelled')),
    idempotency_key TEXT,
    assistant_saved BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS chat_runs_chat_idx ON chat_runs (chat_id, created_at);
CREATE INDEX IF NOT EXISTS chat_runs_tenant_idx ON chat_runs (tenant_id, created_at);
