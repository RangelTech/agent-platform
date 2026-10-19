-- 0050 (spec 10, U-1b/U-1e): o motor deixa de autenticar pessoas. Usuários
-- viram projeção de um User do Chatwoot (mesma pessoa pode estar em várias
-- empresas) e o contexto assinado pelo Rails é a única credencial de UI.

ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS chatwoot_user_id BIGINT;

-- O e-mail deixa de ser único no sistema inteiro: a mesma pessoa existe uma
-- vez por empresa. O master (tenant_id NULL) continua único.
DROP INDEX IF EXISTS users_email_key;
CREATE UNIQUE INDEX IF NOT EXISTS users_tenant_email_key
    ON users (COALESCE(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(email));
CREATE UNIQUE INDEX IF NOT EXISTS users_tenant_chatwoot_user_key
    ON users (tenant_id, chatwoot_user_id) WHERE chatwoot_user_id IS NOT NULL;

-- Anti-repetição do contexto assinado (cada nonce vale uma vez).
CREATE TABLE IF NOT EXISTS ui_nonces (
    nonce TEXT PRIMARY KEY,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ui_nonces_expires_idx ON ui_nonces (expires_at);

-- Estado do provisionamento pedido pelo Chatwoot (uma linha por Account).
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS provisioned_at TIMESTAMPTZ;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS suspended_by_chatwoot BOOLEAN NOT NULL DEFAULT FALSE;
