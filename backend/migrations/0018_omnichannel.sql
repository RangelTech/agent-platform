-- Fase 2 — camada omnichannel (Chatwoot).
--
-- O agent-platform continua sendo o sistema mestre; a coluna abaixo é só o
-- ponteiro canônico para a Account correspondente do Chatwoot. A ponte guarda
-- o resto do estado operacional no banco dela.

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS chatwoot_account_id BIGINT;

-- Novo recurso de permissão para quem já administra integrações.
UPDATE user_profiles
   SET permissions = permissions || '{"omnichannel": ["view", "create", "edit", "delete"]}'::jsonb
 WHERE NOT (permissions ? 'omnichannel')
   AND permissions -> 'integrations' @> '["view", "create", "edit", "delete"]'::jsonb;
