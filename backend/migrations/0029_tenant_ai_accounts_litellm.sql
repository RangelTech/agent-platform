-- Suporta contas de IA sem instância de 9Router por trás (tenant no LiteLLM,
-- infra-04 seção 2d): sem instância própria, não existe "id de conexão
-- remota" pra guardar — a plataforma passa a guardar a própria API key
-- cifrada, e o deployment no LiteLLM só nasce quando um combo a usa.

ALTER TABLE tenant_ai_accounts ALTER COLUMN router_connection_id DROP NOT NULL;
ALTER TABLE tenant_ai_accounts ADD COLUMN api_key_encrypted TEXT;

ALTER TABLE tenant_ai_accounts
    ADD CONSTRAINT tenant_ai_accounts_9router_ou_litellm CHECK (
        router_connection_id IS NOT NULL OR api_key_encrypted IS NOT NULL
    );
