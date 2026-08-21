-- Fase C (infra-04), continuação de 0027 — libera as colunas do 9Router pra
-- aceitar tenant só-LiteLLM.
--
-- Um tenant provisionado direto no LiteLLM não tem instância própria: não
-- existe `base_url` por tenant (é a URL compartilhada, config do serviço,
-- não linha de banco), não existe senha administrativa (o LiteLLM usa
-- master key de serviço, não senha por tenant). Forçar essas 3 colunas a
-- NOT NULL obrigaria gravar dado inventado só pra satisfazer o schema — pior
-- que deixar nullable.
--
-- O CHECK garante a integridade que o NOT NULL dava antes, só que agora
-- cobrindo os dois formatos: uma linha tem que ser um 9Router completo OU um
-- LiteLLM completo, nunca as duas coisas pela metade.

ALTER TABLE tenant_routers ALTER COLUMN base_url DROP NOT NULL;
ALTER TABLE tenant_routers ALTER COLUMN admin_password_encrypted DROP NOT NULL;
ALTER TABLE tenant_routers ALTER COLUMN api_key_encrypted DROP NOT NULL;

ALTER TABLE tenant_routers ADD CONSTRAINT tenant_routers_9router_ou_litellm CHECK (
    (base_url IS NOT NULL AND admin_password_encrypted IS NOT NULL AND api_key_encrypted IS NOT NULL)
    OR
    (litellm_team_id IS NOT NULL AND bridge_key_encrypted IS NOT NULL AND ai_assist_key_encrypted IS NOT NULL)
);
