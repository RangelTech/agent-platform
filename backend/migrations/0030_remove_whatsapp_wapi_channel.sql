-- O canal WhatsApp via W-API embutido no Integrations (0017_whatsapp.sql)
-- foi removido -- terceira implementação WAPI concorrente encontrada no
-- sistema (as outras duas: chatwoot-rt/bridge, já removida, e o
-- Channel::Wapi nativo do Chatwoot, que é o caminho real de verdade agora,
-- produto-05 seção 3). Só tinha integração de teste/regressão, nenhum
-- cliente real (confirmado 21/08/2026).
--
-- `chats.channel`/`chats.external_contact` (0017_whatsapp.sql) NÃO são
-- removidos aqui: são um mecanismo genérico de "conversa de canal externo
-- no Chat", não exclusivo do WhatsApp -- baixo risco manter, alto risco
-- reverter se algo mais já depender deles.
DROP TABLE IF EXISTS whatsapp_webhook_events;
DROP TABLE IF EXISTS whatsapp_connections;
