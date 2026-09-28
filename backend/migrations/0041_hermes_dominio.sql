-- 0041: Hermes agente -- fundação de domínio (Fase A da
-- docs/specs/SPEC_HERMES_INTEGRADO_RIA_ATENDIMENTO.md).
--
-- Modela dispositivos (extensões VS Code pareadas), sessões Hermes por
-- dispositivo, o fluxo de eventos/comandos/aprovações e a trilha de
-- auditoria. Nada aqui fala com o Qwen: essa camada só autentica, autoriza,
-- persiste e audita -- exatamente como a seção 5 (limites de
-- responsabilidade) e a seção 9 (modelo de dados) descrevem.
--
-- Todo `tenant_id` é imutável e obrigatório (seção 7.7): nenhuma linha aqui
-- pode existir sem um dono de tenant, e cada tabela filha carrega o mesmo
-- tenant_id da sua tabela pai em vez de depender só do JOIN para isolar.

CREATE TABLE hermes_devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    -- Dono do pareamento. Um usuário pode ter vários dispositivos; um
    -- dispositivo pertence a exatamente um usuário (seção 7.2).
    user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    platform TEXT,
    extension_version TEXT,
    status TEXT NOT NULL DEFAULT 'connected'
        CHECK (status IN ('connected', 'disconnected', 'revoked')),
    last_seen_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX hermes_devices_tenant_idx ON hermes_devices (tenant_id);
CREATE INDEX hermes_devices_user_idx ON hermes_devices (user_id);

-- Credencial própria do dispositivo (seção 7.2): rotacionável, nunca a senha
-- do usuário, nunca um token OAuth de outro provider. Só o hash fica salvo;
-- igual ao padrão de `sessions.token_hash`.
CREATE TABLE hermes_device_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id UUID NOT NULL REFERENCES hermes_devices (id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);
CREATE INDEX hermes_device_credentials_device_idx ON hermes_device_credentials (device_id);

-- Uma sessão Hermes é um chat/execução aberta num workspace de um
-- dispositivo. `external_session_id` é o id que a própria extensão usa
-- localmente -- o upsert é idempotente por (device_id, external_session_id),
-- então reenviar o mesmo estado nunca duplica a sessão.
CREATE TABLE hermes_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    device_id UUID NOT NULL REFERENCES hermes_devices (id) ON DELETE CASCADE,
    external_session_id TEXT NOT NULL,
    title TEXT,
    workspace_path TEXT,
    repo TEXT,
    branch TEXT,
    provider_name TEXT,
    model_name TEXT,
    status TEXT NOT NULL DEFAULT 'idle' CHECK (
        status IN (
            'idle', 'running', 'waiting_approval', 'completed', 'failed', 'ended'
        )
    ),
    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX hermes_sessions_device_external_key
    ON hermes_sessions (device_id, external_session_id);
CREATE INDEX hermes_sessions_tenant_idx ON hermes_sessions (tenant_id);
CREATE INDEX hermes_sessions_device_idx ON hermes_sessions (device_id);
CREATE INDEX hermes_sessions_status_idx ON hermes_sessions (status);
CREATE INDEX hermes_sessions_updated_idx ON hermes_sessions (updated_at DESC);

-- Trilha de eventos técnicos de uma sessão (envelope da seção 8.2).
-- `sequence` é atribuída pelo dispositivo e a unicidade por
-- (session_id, sequence) é o que torna reenvio/reconexão idempotente
-- (seção 8.3: "eventos duplicados são descartados por event_id/sequência").
CREATE TABLE hermes_session_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    session_id UUID NOT NULL REFERENCES hermes_sessions (id) ON DELETE CASCADE,
    sequence BIGINT NOT NULL,
    type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX hermes_session_events_session_sequence_key
    ON hermes_session_events (session_id, sequence);
CREATE INDEX hermes_session_events_tenant_idx ON hermes_session_events (tenant_id);
CREATE INDEX hermes_session_events_occurred_idx ON hermes_session_events (occurred_at DESC);

-- Comando remoto (seção 8.3). `idempotency_key` é escolhida por quem envia
-- (o RIA) e é única por sessão: reenviar a mesma instrução (retry de rede)
-- devolve o comando já existente em vez de criar um segundo.
CREATE TABLE hermes_commands (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    session_id UUID NOT NULL REFERENCES hermes_sessions (id) ON DELETE CASCADE,
    device_id UUID NOT NULL REFERENCES hermes_devices (id) ON DELETE CASCADE,
    issued_by UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (
        status IN (
            'queued', 'delivered', 'accepted', 'running',
            'completed', 'rejected', 'failed', 'cancelled', 'expired'
        )
    ),
    result JSONB,
    error TEXT,
    deadline_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX hermes_commands_session_idempotency_key
    ON hermes_commands (session_id, idempotency_key);
CREATE INDEX hermes_commands_tenant_idx ON hermes_commands (tenant_id);
CREATE INDEX hermes_commands_device_status_idx ON hermes_commands (device_id, status);
CREATE INDEX hermes_commands_session_idx ON hermes_commands (session_id);

-- Solicitação de aprovação que o Hermes declara (seção 6.4/8.2). Pode ou não
-- estar associada a um comando específico.
CREATE TABLE hermes_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    session_id UUID NOT NULL REFERENCES hermes_sessions (id) ON DELETE CASCADE,
    command_id UUID REFERENCES hermes_commands (id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    decided_by UUID REFERENCES users (id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ,
    justification TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX hermes_approvals_tenant_idx ON hermes_approvals (tenant_id);
CREATE INDEX hermes_approvals_session_idx ON hermes_approvals (session_id);
CREATE INDEX hermes_approvals_status_idx ON hermes_approvals (status);

-- Handoff estruturado entre notebooks (seção 10). Entra em uso na Fase E;
-- a tabela já nasce agora para não exigir migração nova quando a fase abrir.
CREATE TABLE hermes_contexts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    source_session_id UUID NOT NULL REFERENCES hermes_sessions (id) ON DELETE CASCADE,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX hermes_contexts_tenant_idx ON hermes_contexts (tenant_id);
CREATE INDEX hermes_contexts_source_session_idx ON hermes_contexts (source_session_id);

CREATE TABLE hermes_context_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    context_id UUID NOT NULL REFERENCES hermes_contexts (id) ON DELETE CASCADE,
    version INT NOT NULL,
    diff JSONB NOT NULL DEFAULT '{}'::jsonb,
    applied_to_session_id UUID REFERENCES hermes_sessions (id) ON DELETE SET NULL,
    applied_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX hermes_context_versions_context_version_key
    ON hermes_context_versions (context_id, version);
CREATE INDEX hermes_context_versions_tenant_idx ON hermes_context_versions (tenant_id);

-- Auditoria de produto (não o log técnico de eventos): quem fez o quê, de
-- qual dispositivo, com qual resultado (seção 2 e critério de aceite 15).
CREATE TABLE hermes_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('user', 'device', 'system')),
    actor_id TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id UUID,
    result TEXT NOT NULL CHECK (result IN ('ok', 'denied', 'error')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX hermes_audit_log_tenant_idx ON hermes_audit_log (tenant_id);
CREATE INDEX hermes_audit_log_resource_idx ON hermes_audit_log (resource_type, resource_id);
CREATE INDEX hermes_audit_log_created_idx ON hermes_audit_log (created_at DESC);
