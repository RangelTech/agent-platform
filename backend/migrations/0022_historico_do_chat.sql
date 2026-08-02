-- 0022: quanto do histórico vai para o modelo, e se o começo é resumido.
--
-- Até aqui a conversa inteira ia para o modelo a cada turno, sem corte nem
-- resumo: o custo crescia junto com a conversa e, longa o bastante, ela batia
-- no limite do modelo e falhava em vez de degradar.
--
-- As duas opções são do template porque afetam qualidade, e essa escolha é do
-- dono do agente: cortar é barato e perde o começo; resumir preserva o sentido
-- mas custa uma chamada a mais e depende do resumo estar bom.

ALTER TABLE template_versions
    -- Quantas mensagens recentes seguem para o modelo.
    ADD COLUMN IF NOT EXISTS history_limit INTEGER NOT NULL DEFAULT 100,
    -- Se ligado, ao invés de descartar o começo, ele é resumido e o resumo
    -- entra como uma mensagem só.
    ADD COLUMN IF NOT EXISTS compress_history BOOLEAN NOT NULL DEFAULT FALSE;
