-- Hermes Relay (SPEC_HERMES_INTEGRADO_RIA_ATENDIMENTO.md, secao 7.2/8.1):
-- ticket de curta duracao e uso unico para o handshake WSS. A extensao ja
-- tem uma credencial de dispositivo de longa duracao (hermes_device_credentials,
-- migration 0041); em vez de mandar essa credencial direto na URL do
-- WebSocket (fica em log de acesso do proxy/Traefik), ela troca a
-- credencial por um ticket descartavel via HTTP autenticado, e usa o
-- ticket -- nunca a credencial -- na query string do WS.
CREATE TABLE hermes_ws_tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    device_id UUID NOT NULL REFERENCES hermes_devices (id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX hermes_ws_tickets_device_idx ON hermes_ws_tickets (device_id);
-- Tickets expirados/usados se acumulam rapido (um por reconexao); sem
-- retencao de longo prazo (o proprio uso e o dado, nao o historico).
CREATE INDEX hermes_ws_tickets_expires_idx ON hermes_ws_tickets (expires_at);
