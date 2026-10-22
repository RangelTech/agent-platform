-- Spec 11 §4: arquivos pertencem à skill da empresa; agente escolhe skills (N:N).
CREATE TABLE IF NOT EXISTS skill_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    skill_id UUID NOT NULL REFERENCES tenant_skills (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    mime TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes BIGINT NOT NULL,
    sha256 TEXT NOT NULL,
    data BYTEA NOT NULL,
    created_by UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (skill_id, name)
);
CREATE INDEX IF NOT EXISTS skill_files_tenant_idx ON skill_files (tenant_id);

CREATE TABLE IF NOT EXISTS agent_skills (
    template_id UUID NOT NULL REFERENCES templates (id) ON DELETE CASCADE,
    skill_id UUID NOT NULL REFERENCES tenant_skills (id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (template_id, skill_id)
);
CREATE INDEX IF NOT EXISTS agent_skills_skill_idx ON agent_skills (skill_id);

-- Migração sem mudar comportamento: todas as skills ativas vinculadas a todos os agentes por skills.
INSERT INTO agent_skills (template_id, skill_id, tenant_id)
SELECT t.id, s.id, s.tenant_id
  FROM templates t JOIN tenant_skills s ON s.tenant_id = t.tenant_id
 WHERE t.agent_kind = 'skills' AND s.status = 'active'
ON CONFLICT DO NOTHING;
