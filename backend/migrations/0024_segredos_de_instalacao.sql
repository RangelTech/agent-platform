-- 0024: segredos de instalação no nosso banco, para sair do Secret Manager.
--
-- Segredo de TENANT já vivia aqui (`secrets`, `payments_credentials`,
-- `ai_services`). O que continuava espalhado em variável de ambiente por
-- serviço era o segredo de INSTALAÇÃO: app da Meta, chave do Serper, chaves do
-- S3, tokens de serviço. Cada um deles obrigava um redeploy para mudar e
-- prendia a instalação ao cofre da nuvem em que ela nasceu.
--
-- O que NÃO vem para cá é o bootstrap — `DATABASE_URL` e `ENCRYPTION_KEY`.
-- Nenhum sistema guarda no banco o segredo que abre o banco. Ver
-- docs/specs/segredos-no-banco.md.

CREATE TABLE IF NOT EXISTS installation_secrets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    value_encrypted TEXT NOT NULL,
    -- Qual chave mestra cifrou este valor. Sem isto, rotacionar a chave vira
    -- migração de emergência: seria impossível saber o que já foi reescrito.
    key_id TEXT NOT NULL DEFAULT 'k1',
    -- Para onde este segredo precisa ser propagado. Vazio = só nós usamos.
    targets JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Monotônica: é o que deixa o sincronizador saber se o destino está
    -- atualizado sem comparar valores em claro.
    version INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Estado da propagação em tabela própria, e não como coluna do segredo: um
-- segredo pode estar certo aqui e não ter chegado no destino. Se fosse coluna,
-- cada retentativa reescreveria a linha do segredo e um erro de propagação
-- ficaria indistinguível de uma edição.
CREATE TABLE IF NOT EXISTS secret_sync_state (
    secret_id UUID NOT NULL REFERENCES installation_secrets (id) ON DELETE CASCADE,
    target TEXT NOT NULL,
    synced_version INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    detail TEXT NOT NULL DEFAULT '',
    attempted_at TIMESTAMPTZ,
    PRIMARY KEY (secret_id, target)
);

CREATE INDEX IF NOT EXISTS secret_sync_state_pendentes_idx
    ON secret_sync_state (status)
    WHERE status <> 'ok';
