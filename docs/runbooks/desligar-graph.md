# Desligar os agentes `graph` de uma empresa (Fase L-2)

Os agentes estruturados (`graph`) foram descontinuados. O desligamento é **por empresa**, com backup e sem apagar nada.

## O que a flag faz
`tenants.graph_enabled` (nasce `true`; só o master altera: `PUT /api/tenants/{id}` com `{"graph_enabled": false}`).
Com a flag desligada:

| Ação | Resultado |
|---|---|
| Nova conversa ou mensagem em conversa `llm`/sem template, API pública sem template skills | `403` "Os agentes estruturados (graph) foram descontinuados…" |
| Criar template `structured`, nova versão, Assistente RAgentes (cria `graph`) | `403` |
| Lista de templates da empresa | esconde os `structured` |
| Histórico (`GET /api/chats*`, mensagens, artefatos, memórias, checkpoints) | **continua legível**, nada é movido |
| Agentes por skills | **não mudam** |

`/api/auth/me` devolve `graph_agents_enabled`; o master vê `graph_enabled` em `/api/tenants`.

## Procedimento recomendado (uma empresa por vez)
1. `python scripts/graph_desligar.py --empresa <tenant_key> --simular`
   Gera o backup e o **verifica** (hash e linhas de cada arquivo), sem ligar a quarentena. Confira as contagens.
2. `python scripts/graph_desligar.py --empresa <tenant_key> --backup-dir /srv/backups/graph`
   Refaz o backup, verifica e só então liga a quarentena (`graph_enabled=false` + linha em `graph_shutdown_log`).
3. Reverter: `python scripts/graph_desligar.py --empresa <tenant_key> --reativar` (a flag volta; nenhum dado saiu do lugar).

O script **recusa** (código 3) se uma caixa de entrada (`inbox_ai_links`) ou rotina ativa (`scheduled_jobs`) ainda aponta para
template `structured`; migre-as antes ou use `--forcar` conscientemente.

## O que entra no backup (`<backup-dir>/<tenant_key>-<carimbo>/`)
`templates`, `template_versions`, `template_agents` (só `structured`), `chats` e `chat_messages` (kernel `llm`), `chat_runs`,
`tool_calls`, `artifacts` (metadados; os arquivos continuam no storage), `memories`, `memory_extraction_state` e as tabelas do
LangGraph `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` (quando existem). Um `.jsonl.gz` por tabela + `manifest.json`
com contagens e SHA-256. Valores binários vão em `{"__bytes_b64__": …}`; vetores como texto.

## Quarentena = flag, não cópia
Os dados **não são movidos nem apagados**: a quarentena é a recusa de novas execuções. O backup é a rede de segurança.

## Estado da frota (Fase L)
O desligamento de todas as empresas é pré-condição do L-3 (parar o container `kernel-llm`).
Veja `specs/anexos/10/runbooks/l3-parar-kernel-llm.md`.
