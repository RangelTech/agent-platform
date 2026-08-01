-- Fase 3 — camada de modelos por tenant sobre o 9Router.
--
-- Decisão de arquitetura (medida, não suposta): o 9Router **não tem
-- multi-tenancy**. `apiKeys` não tem dono, combos são globais por instância e
-- a escolha da conta que atende uma chamada é feita por
-- `(provider, isActive, priority)` — não existe forma de fixar a conta de um
-- combo. Numa instância compartilhada, a conta OAuth de um cliente atenderia
-- a chamada de outro, e nenhuma regra nossa impediria isso, porque a escolha
-- acontece dentro do 9Router.
--
-- Por isso: **uma instância por tenant**. O isolamento passa a ser uma
-- propriedade do deploy (processo e volume separados), não disciplina de
-- código. Medido em laboratório: 105 MB de RAM com 500 chaves e 200 combos,
-- listagem em 33 ms — o custo por tenant é baixo e o dado por tenant é mínimo.

CREATE TABLE IF NOT EXISTS tenant_routers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL UNIQUE REFERENCES tenants (id) ON DELETE CASCADE,
    -- URL da instância dedicada do tenant (privada, só a plataforma chama).
    base_url TEXT NOT NULL,
    -- Credenciais da instância. A senha administra; a chave consome modelos.
    -- Nenhuma das duas volta pela API, nem para o próprio tenant.
    admin_password_encrypted TEXT NOT NULL,
    api_key_encrypted TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Conta de IA do cliente, espelhada do 9Router dele.
CREATE TABLE IF NOT EXISTS tenant_ai_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    -- id da conexão dentro do 9Router do tenant.
    router_connection_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    auth_type TEXT NOT NULL DEFAULT 'apikey',
    label TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, router_connection_id)
);

-- Combo: quais modelos revezam para atender. Vira um serviço de IA do tenant,
-- então o editor de template não muda nada para usá-lo.
CREATE TABLE IF NOT EXISTS tenant_ai_combos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    -- Nome dentro do 9Router; prefixado com o tenant para leitura humana.
    router_combo_name TEXT NOT NULL,
    models JSONB NOT NULL DEFAULT '[]'::jsonb,
    ai_service_id UUID REFERENCES ai_services (id) ON DELETE SET NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

-- Novo recurso de permissão para quem já administra serviços de IA.
UPDATE user_profiles
   SET permissions = permissions || '{"ai_router": ["view", "create", "edit", "delete"]}'::jsonb
 WHERE NOT (permissions ? 'ai_router')
   AND permissions -> 'ai_services' @> '["view", "create", "edit", "delete"]'::jsonb;
