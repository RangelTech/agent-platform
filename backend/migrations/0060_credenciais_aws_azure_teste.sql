-- Spec 11 §7: novos tipos de credencial (AWS, Azure) e resultado do ultimo teste.
ALTER TABLE secrets DROP CONSTRAINT IF EXISTS secrets_kind_check;
ALTER TABLE secrets ADD CONSTRAINT secrets_kind_check
    CHECK (kind IN ('secret', 'ssh', 'gcp', 'aws', 'azure'));
ALTER TABLE secrets ADD COLUMN IF NOT EXISTS last_test_at TIMESTAMPTZ;
ALTER TABLE secrets ADD COLUMN IF NOT EXISTS last_test_ok BOOLEAN;
ALTER TABLE secrets ADD COLUMN IF NOT EXISTS last_test_detail TEXT;
