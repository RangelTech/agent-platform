-- Produto-15 secao 6a -- credencial capturada pelo RAtende Connector
-- (extensao de navegador) pra providers "nao oficiais" (Instagram/Facebook/
-- TikTok, sessao de navegador em vez de OAuth). So captura e armazenamento
-- nesta fase -- consumo real (ler/mandar mensagem) fica pra uma spec de
-- integracao com o chatwoot-rt, fora de escopo aqui.

CREATE TABLE tenant_unofficial_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,             -- 'instagram_web' | 'facebook_web' | 'tiktok_web'
    label TEXT NOT NULL,                -- nome editavel, ex. "TikTok principal"
    external_label TEXT,                -- @handle detectado, nao sensivel
    cookies_encrypted TEXT NOT NULL,    -- JSON dos cookies, cifrado
    status TEXT NOT NULL DEFAULT 'connected',  -- connected | reauth_required | error
    last_validated_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tenant_unofficial_connections_tenant
    ON tenant_unofficial_connections (tenant_id) WHERE is_active;

-- Recurso de permissao novo -- backfill JA NESTA MIGRATION (produto-08 §11
-- foi o bug de esquecer isso; nao repetir, ja e o 3o recurso seguido que
-- nasce com o backfill certo desde o inicio).
UPDATE user_profiles
   SET permissions = permissions || '{"unofficial_connections": ["view", "create", "edit", "delete"]}'::jsonb
 WHERE NOT (permissions ? 'unofficial_connections')
   AND permissions -> 'templates' @> '["view", "create", "edit", "delete"]'::jsonb;
