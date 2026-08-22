-- Product 07: system-owned, tenant-scoped onboarding assistant and its audit.
ALTER TABLE templates ADD COLUMN IF NOT EXISTS system_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS templates_tenant_system_key_idx
    ON templates (tenant_id, system_key)
    WHERE system_key IS NOT NULL AND NOT is_deleted;

CREATE TABLE IF NOT EXISTS tenant_guide_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    chat_id UUID REFERENCES chats(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    plan JSONB,
    result JSONB,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tenant_guide_audit_tenant_idx
    ON tenant_guide_audit (tenant_id, created_at DESC);
