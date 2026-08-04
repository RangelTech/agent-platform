# Fechamento das pendências antes do primeiro cliente

Data: 04/08/2026. Continua
`docs/handoffs/2026-08-03-validacao-independente.md`, que listou sete
pendências por risco. Esta rodada fechou seis; a sétima é decisão do dono.

## 1. O que foi entregue

### 1.1 Um agente por caixa de atendimento

Era o furo mais visível para quem usa: uma empresa tem várias caixas — WhatsApp
da cobrança, Instagram das vendas, widget do site — e todas eram atendidas pelo
mesmo template, porque a configuração por caixa existia no banco da ponte e não
tinha tela nenhuma na frente. O único jeito de mudar era chamar a API na mão, o
que na prática significa que ninguém mudava.

- ponte: `GET /admin/ai-config/{tenant}` lista as caixas **do Chatwoot**, não as
  que a ponte criou. Caixa conectada dentro do próprio Chatwoot (um Instagram
  ligado pela tela) nunca passou pela ponte — e é justamente nela que alguém vai
  querer a IA;
- ponte: configurar caixa nova **herda a chave de integração** do tenant. Quem
  escolhe um template numa tela não sabe que existe uma chave por trás; sem a
  herança a segunda caixa ficaria configurada e muda;
- plataforma: `GET /api/omnichannel/inboxes` e `PUT .../inboxes/{id}/ia`, com
  recusa de template de outro tenant — sem essa checagem, um id colado de outra
  empresa colocaria a IA de um cliente no canal de outro;
- tela: cada caixa com um seletor de agente, e "Somente atendimento humano" como
  opção explícita.

### 1.2 Guardrails de runtime

| Guardrail | O que fazia antes |
|---|---|
| Turno sem texto | `done` com texto vazio — indistinguível de resposta entregue. Aconteceu em campo (C1 t11) e o harness marcou como turno bom. Agora é `error: empty_answer` e log `EMPTY_ANSWER` |
| Teto de chamadas por turno | `specialist_max_tool_rounds` limita um especialista; o supervisor chama vários. Medido: 30 chamadas e 26 datasets para desenhar um gráfico de linha. Agora `max_tool_calls_per_turn` (padrão 40) conta o turno inteiro e avisa o modelo ao estourar |
| Gateway de pagamento fora do ar | Sem teste. Agora dois: cobrança e consulta com gateway inacessível precisam virar erro legível, nunca "cobrança criada" nem status inventado |

### 1.3 Segredos de instalação no nosso banco

Implementa `docs/specs/segredos-no-banco.md` (fases 1 a 3):

- migração 0024: `installation_secrets` (com `key_id` e `version`) e
  `secret_sync_state` em tabela separada;
- resolvedor `app/installation_secrets.py`: **banco antes do ambiente**;
- rotas master-only; o valor nunca volta, nem para o master;
- bootstrap (`DATABASE_URL`, `ENCRYPTION_KEY`, `SECRET_KEY_BASE`) é recusado no
  cadastro — aceitar criaria uma linha que nada lê e a falsa sensação de que o
  Secret Manager já pode ser desligado;
- tela "Chaves da instalação", com atalhos para as chaves da Meta e do Serper e
  o estado da propagação visível;
- Chatwoot: `scripts/ops/sync_installation_config.rb` grava no
  `InstallationConfig` e **confirma o que aplicou**. O `deploy.sh` parou de
  injetar `FB_*`/`IG_*` como env — o `GlobalConfigService` faz
  `ENV.fetch(chave) { banco }`, então enquanto a env existisse a chave
  cadastrada na tela não teria efeito nenhum.

Trocar uma chave de canal deixou de exigir redeploy: `./infra/deploy.sh
sync-secrets` no repo do Chatwoot.

### 1.4 Correções da validação anterior

- guarda que recusa rodar a suíte destrutiva contra banco remoto;
- `/health/ready` público sem diagnóstico no corpo;
- guarda de deploy enxergando arquivo novo não rastreado.

### 1.5 Memórias de QA

**675 apagadas** do tenant `31445557-…` (eram 651 antes de a última rodada de C2
somar mais). A marca de leitura por conversa fica: com ela, conversa antiga não
volta a virar memória.

## 2. O que continua aberto

### 2.1 Credenciais da Meta (bloqueio externo)

O caminho está pronto de ponta a ponta — cadastro na tela, cifragem,
propagação para o `InstallationConfig`, canal ligando sem redeploy. O que falta
**não é código**: é criar o app Business no painel da Meta, passar pelo App
Review e ter as três chaves. Isso exige a conta da Meta do dono. Enquanto não
existirem, Instagram e Messenger seguem escondidos, agora por falta de chave e
não por falta de mecanismo.

Depois de ter as chaves: cadastrar em *Chaves da instalação*, marcar "enviar
também para o atendimento", e conferir o estado "Propagado" na própria tela.

### 2.2 Decisões do dono (a sétima pendência, intocada)

- W-API × WhatsApp Cloud API oficial;
- paleta do Chatwoot: aceitar o padrão × assumir fork;
- `INSTALLATION_NAME` ainda é "Chatwoot";
- senha `123456` das instâncias 9Router expostas.

## 3. Uma decisão que mantive contra o seu incômodo

A guarda `ALLOW_DESTRUCTIVE_TESTS` ficou. O motivo é estreito e vale escrever:
a suíte do backend roda `TRUNCATE tenants, users, user_profiles, sessions
CASCADE`, e diagnosticar produção exige exportar o `DATABASE_URL` real no mesmo
shell. Sem a guarda, um `pytest` distraído apaga a LicitaEnterprisse, os tenants
de demonstração e todo o histórico de QA — em produção, que é onde estamos
testando por decisão consciente.

Ela não atrapalha ninguém: banco local e serviço de CI passam direto, e nenhum
comando desta rodada precisou da variável. O que ela impede é o acidente que só
acontece uma vez.
