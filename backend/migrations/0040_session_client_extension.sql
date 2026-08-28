-- 28/08/2026, pedido do dono: sessão da extensão (RAtende Connector,
-- produto-15) nunca deve expirar por inatividade -- ela roda em segundo
-- plano, capturando cookie sem interação humana constante, então o
-- timeout de 8h (`session_idle_minutes`) pensado pro painel web derruba
-- ela sem aviso. Distinguir por `client` (extensão manda `client:
-- "extension"` no login, 1 linha nova em `ratende-connector/src/lib/api.ts`)
-- em vez de mudar o timeout geral -- sessão do painel web continua com
-- o mesmo comportamento de sempre.
ALTER TABLE sessions ADD COLUMN client TEXT;
