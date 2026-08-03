-- 0023: teto do que uma ferramenta devolve para dentro do prompt do especialista.
--
-- Medido numa conversa de 30 turnos: o especialista `despesas` acumulou 889 mil
-- tokens de prompt, contra 10 mil do supervisor. O histórico da conversa, que
-- já tem corte e resumo desde a 0022, é o problema MENOR — o gasto está no
-- resultado das ferramentas, que volta inteiro e reentra no contexto a cada
-- rodada seguinte do especialista.
--
-- Cortar não perde dado: o payload continua materializado no artefato e o
-- artifact_id encadeia para gráfico, planilha e sandbox sem passar pelo modelo.
-- O valor é do template porque depende do agente: quem analisa texto precisa de
-- mais folga que quem só agrega números.

ALTER TABLE template_versions
    ADD COLUMN IF NOT EXISTS tool_output_limit INTEGER NOT NULL DEFAULT 24000;
