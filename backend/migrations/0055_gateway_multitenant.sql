-- Fase G-2: o gateway compartilhado tem tenant e chaves próprios. As colunas
-- LiteLLM permanecem durante a migração, mas um novo tenant nasce no gateway.

ALTER TABLE tenant_routers ADD COLUMN gateway_tenant_id TEXT;
ALTER TABLE tenant_routers ADD COLUMN gateway_bridge_key_encrypted TEXT;
ALTER TABLE tenant_routers ADD COLUMN gateway_ai_assist_key_encrypted TEXT;

ALTER TABLE tenant_routers DROP CONSTRAINT IF EXISTS tenant_routers_9router_ou_litellm;
ALTER TABLE tenant_routers ADD CONSTRAINT tenant_routers_um_gateway_valido CHECK (
    (base_url IS NOT NULL AND admin_password_encrypted IS NOT NULL AND api_key_encrypted IS NOT NULL)
    OR
    (litellm_team_id IS NOT NULL AND bridge_key_encrypted IS NOT NULL AND ai_assist_key_encrypted IS NOT NULL)
    OR
    (gateway_tenant_id IS NOT NULL AND gateway_bridge_key_encrypted IS NOT NULL
     AND gateway_ai_assist_key_encrypted IS NOT NULL)
);

CREATE UNIQUE INDEX tenant_routers_gateway_tenant_id_uq
    ON tenant_routers (gateway_tenant_id) WHERE gateway_tenant_id IS NOT NULL;
