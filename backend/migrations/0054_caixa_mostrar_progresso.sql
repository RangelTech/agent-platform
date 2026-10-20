-- 0054: por caixa de entrada, permite enviar UMA mensagem curta de progresso ("Pensando...")
-- antes da resposta final. Padrão desligado: canal externo recebe só a resposta final.

ALTER TABLE inbox_ai_links
    ADD COLUMN IF NOT EXISTS show_progress BOOLEAN NOT NULL DEFAULT FALSE;
