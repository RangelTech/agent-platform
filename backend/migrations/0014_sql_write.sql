-- 0014: SQL write support per template version — table allowlist and the
-- user-confirmation flag (default on).
ALTER TABLE template_versions
    ADD COLUMN write_tables JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE template_versions
    ADD COLUMN require_write_confirmation BOOLEAN NOT NULL DEFAULT TRUE;
