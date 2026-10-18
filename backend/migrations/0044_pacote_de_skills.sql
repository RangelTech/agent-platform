-- 0044: pacote de arquivos Markdown do agente por skills (spec 04, 8.1).

-- Arquivos de uma VERSAO (imutavel). kind: agent (AGENTE.md), skill
-- (SKILL.md), reference, script, template.
CREATE TABLE IF NOT EXISTS template_skill_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID NOT NULL REFERENCES template_versions (id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('agent', 'skill', 'reference', 'script', 'template')),
    content TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    UNIQUE (version_id, path)
);

-- Rascunho em edicao (1 por template). A versao continua imutavel.
CREATE TABLE IF NOT EXISTS template_skill_drafts (
    template_id UUID PRIMARY KEY REFERENCES templates (id) ON DELETE CASCADE,
    files JSONB NOT NULL DEFAULT '[]'::jsonb,
    manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_by UUID REFERENCES users (id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Credenciais autorizadas por versao (grants). ref_id NULL = qualquer do tipo.
CREATE TABLE IF NOT EXISTS template_version_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID NOT NULL REFERENCES template_versions (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    ref_id UUID
);
CREATE INDEX IF NOT EXISTS template_version_credentials_idx
    ON template_version_credentials (version_id);
