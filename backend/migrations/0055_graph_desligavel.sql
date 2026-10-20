-- 0055: desligamento por empresa dos agentes `graph` (estruturados) -- Fase L-2.
--
-- graph_enabled: liga/desliga os agentes estruturados da empresa (so o master altera).
-- Nasce LIGADO para nao mudar o comportamento de quem ja usa; o desligamento e feito
-- por empresa, pelo script scripts/graph_desligar.py, que antes grava backup e deixa
-- o registro abaixo. Nada e apagado: chats, mensagens, checkpoints, memorias,
-- artefatos e templates continuam no banco (legiveis), so deixam de aceitar novas
-- execucoes.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS graph_enabled BOOLEAN NOT NULL DEFAULT true;

CREATE TABLE IF NOT EXISTS graph_shutdown_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('desligar', 'reativar')),
    backup_path TEXT,
    backup_sha256 TEXT,
    counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS graph_shutdown_log_tenant_idx ON graph_shutdown_log (tenant_id, created_at);
