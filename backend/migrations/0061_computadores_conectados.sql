-- Spec 11 §26: computadores conectados (conector SSH). A chave privada da conexao fica cifrada
-- aqui e e injetada por baixo dos panos como variaveis do agente; nunca volta ao navegador.
CREATE TABLE IF NOT EXISTS hosts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    os TEXT,
    arch TEXT,
    conn_pub TEXT NOT NULL,
    conn_key_encrypted TEXT NOT NULL,
    created_by UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE UNIQUE INDEX IF NOT EXISTS hosts_tenant_name_idx ON hosts (tenant_id, lower(name)) WHERE NOT is_deleted;

CREATE TABLE IF NOT EXISTS agent_hosts (
    template_id UUID NOT NULL REFERENCES templates (id) ON DELETE CASCADE,
    host_id UUID NOT NULL REFERENCES hosts (id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    PRIMARY KEY (template_id, host_id)
);
