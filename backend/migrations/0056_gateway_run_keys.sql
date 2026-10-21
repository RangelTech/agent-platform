-- Fase G-9: chave do gateway restrita a (tenant do gateway, combo), por run.
-- O gateway só devolve a chave na criação; guardamos cifrada para reaproveitar.
CREATE TABLE IF NOT EXISTS gateway_run_keys (
    id BIGSERIAL PRIMARY KEY,
    gateway_tenant_id TEXT NOT NULL,
    combo TEXT NOT NULL,
    gateway_key_id TEXT,
    key_encrypted TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (gateway_tenant_id, combo)
);
