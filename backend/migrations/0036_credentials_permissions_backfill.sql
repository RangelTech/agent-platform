-- Produto-08 (mega-spec-reestrutura) secao 11 -- bug real: email_accounts e
-- google_accounts foram adicionados a RESOURCES (permissions.py) pelas
-- migrations 0034/0035, mas -- diferente do padrao usado em 0015/0016/0018/
-- 0020 (sempre fazem UPDATE user_profiles SET permissions = ... pra dar a
-- permissao nova aos admins ja existentes) -- essas duas nao fizeram esse
-- backfill. ADMIN_PERMISSIONS so e aplicado "on creation" de tenant novo,
-- entao admins de tenants antigos ficaram sem ver as telas de Contas de
-- Email/Google no menu. Mesmo padrao das migrations anteriores, sem risco
-- de regressao.

UPDATE user_profiles
   SET permissions = permissions || '{"email_accounts": ["view", "create", "edit", "delete"]}'::jsonb
 WHERE NOT (permissions ? 'email_accounts')
   AND permissions -> 'templates' @> '["view", "create", "edit", "delete"]'::jsonb;

UPDATE user_profiles
   SET permissions = permissions || '{"google_accounts": ["view", "create", "edit", "delete"]}'::jsonb
 WHERE NOT (permissions ? 'google_accounts')
   AND permissions -> 'templates' @> '["view", "create", "edit", "delete"]'::jsonb;
