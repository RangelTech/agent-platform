-- 0021: marca até onde a extração de memória já leu cada conversa.
--
-- A extração roda ao fim de cada turno sobre uma janela das últimas mensagens.
-- Como a janela é maior que um turno, o mesmo trecho era lido de novo a cada
-- turno seguinte e virava fato novo com outra redação — a deduplicação por
-- similaridade não pega reescrita. Medido numa conversa real: 215 memórias
-- para ~60 turnos, boa parte dizendo a mesma coisa.
--
-- Guardando quantas mensagens já foram lidas, cada troca é extraída uma vez.

CREATE TABLE IF NOT EXISTS memory_extraction_state (
    thread_id TEXT PRIMARY KEY,
    messages_read INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
