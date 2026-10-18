-- 0045: skills do usuario (camada 3) e auditoria; permissao `skills`.

CREATE TABLE IF NOT EXISTS user_skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    template_id UUID REFERENCES templates (id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('agent', 'user')),
    status TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (status IN ('active', 'pending_review', 'disabled', 'discarded')),
    revision INTEGER NOT NULL DEFAULT 1,
    origin_chat_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Um nome por (empresa, usuario, template); template NULL conta como um valor.
CREATE UNIQUE INDEX IF NOT EXISTS user_skills_unique_name
    ON user_skills (tenant_id, user_id,
                    COALESCE(template_id, '00000000-0000-0000-0000-000000000000'::uuid), name);
CREATE INDEX IF NOT EXISTS user_skills_owner_idx ON user_skills (tenant_id, user_id);

CREATE TABLE IF NOT EXISTS skill_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    user_id UUID,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    skill_ref TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS skill_audit_tenant_idx ON skill_audit (tenant_id, created_at);

-- Recurso de permissao novo: backfill ja nesta migracao (perfis Administrador).
UPDATE user_profiles
   SET permissions = permissions || '{"skills": ["view", "create", "edit", "delete"]}'::jsonb
 WHERE NOT (permissions ? 'skills')
   AND permissions -> 'templates' @> '["view", "create", "edit", "delete"]'::jsonb;
