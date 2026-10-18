-- 0046: confirmacao autenticada de acoes sensiveis e auditoria por ferramenta.

CREATE TABLE IF NOT EXISTS approvals (
    approval_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL,
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    user_id UUID REFERENCES users (id) ON DELETE SET NULL,
    chat_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    risk TEXT NOT NULL DEFAULT 'medium' CHECK (risk IN ('low', 'medium', 'high')),
    kind TEXT NOT NULL DEFAULT 'action',
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'denied', 'expired')),
    decided_by UUID REFERENCES users (id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS approvals_user_pending_idx ON approvals (user_id, status);
CREATE INDEX IF NOT EXISTS approvals_run_idx ON approvals (run_id);

-- Uso por ferramenta; nunca guarda valores de credencial.
CREATE TABLE IF NOT EXISTS tool_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    user_id UUID,
    chat_id TEXT,
    template_id UUID,
    tool TEXT NOT NULL,
    credential_kind TEXT,
    ok BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tool_audit_tenant_idx ON tool_audit (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS tool_audit_run_idx ON tool_audit (run_id);
