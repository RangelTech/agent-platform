# Spec — segredos no nosso banco, sem Secret Manager

Status: proposta. Autor: sessão de QA de 03/08/2026.

## 1. O que se quer, e o que é impossível

Guardar chaves (Meta, W-API, provedores de IA, S3) no nosso banco, cadastradas
pela nossa tela, e fazer com que o Chatwoot passe a usá-las **sem variável de
ambiente** — no provisionamento e também quando a chave é cadastrada depois.
Objetivo declarado: sair do Google Secret Manager, com liberdade de trocar a GCP
por outra nuvem e, no máximo, usar um Infisical.

Uma parte disso é impossível e precisa estar dita antes do desenho: **um sistema
não consegue guardar no banco o segredo que ele precisa para abrir o banco.** Se
a senha do Postgres e a chave mestra de cifragem ficassem lá dentro, o serviço
não teria como subir. Sempre sobra um conjunto mínimo — o *bootstrap* — que vem
de fora.

Então a meta realista, e a que esta spec persegue:

> Reduzir de 26 segredos espalhados no Secret Manager para **2 por serviço**
> (DSN do banco e chave mestra), entregues por qualquer mecanismo de ambiente —
> variável do Cloud Run hoje, Infisical, arquivo na VPS ou Vault amanhã. Todo o
> resto vive no nosso banco, cifrado, editável pela nossa tela.

Isso é o que torna a saída da GCP um dia de trabalho em vez de uma migração.

## 2. Taxonomia — quatro tipos, e só um pode ir para o banco de imediato

| Tipo | Exemplos | Onde vive | Por quê |
|---|---|---|---|
| **Bootstrap** | `DATABASE_URL`, `ENCRYPTION_KEY`, `SECRET_KEY_BASE`, `REDIS_URL` | Ambiente (Infisical/env) | Necessário antes de existir banco ou cifragem |
| **Instalação** | Meta App (`FB_APP_ID`, `FB_APP_SECRET`, verify tokens), Serper, S3, tokens de serviço | Nosso banco | Um por instalação, hoje em env por serviço |
| **Tenant** | Credencial Mercado Pago, chaves de provedores de IA, senha de datasource | Nosso banco | **Já está lá** (`secrets`, `payments_credentials`, `ai_services`) |
| **Canal** | Token W-API por instância, page token da Meta por inbox | Nosso banco / Chatwoot | Por inbox, e o Chatwoot também precisa dele |

O trabalho novo é o tipo **Instalação** — e a sincronização do tipo **Canal**
com o Chatwoot. Tenant já funciona: `backend/app/crypto.py` cifra com Fernet e
`GET` nunca devolve o valor.

## 3. Modelo de dados

Uma tabela nova, `installation_secrets`, irmã da `secrets` que já existe:

```sql
CREATE TABLE installation_secrets (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL UNIQUE,          -- FB_APP_ID, SERPER_API_KEY...
    value_encrypted TEXT NOT NULL,
    key_id       TEXT NOT NULL DEFAULT 'k1',    -- qual chave mestra cifrou
    -- Para onde este segredo precisa ser propagado. Vazio = só nós usamos.
    targets      JSONB NOT NULL DEFAULT '[]',   -- ["chatwoot"]
    version      INTEGER NOT NULL DEFAULT 1,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_by   UUID,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Estado da propagação, separado do segredo: um segredo pode estar correto
-- aqui e não ter chegado no destino, e isso precisa ser visível em vez de
-- descoberto quando um canal não conecta.
CREATE TABLE secret_sync_state (
    secret_id    UUID NOT NULL REFERENCES installation_secrets(id) ON DELETE CASCADE,
    target       TEXT NOT NULL,                 -- chatwoot
    synced_version INTEGER,
    status       TEXT NOT NULL DEFAULT 'pending', -- pending|ok|error
    detail       TEXT NOT NULL DEFAULT '',
    attempted_at TIMESTAMPTZ,
    PRIMARY KEY (secret_id, target)
);
```

Três decisões embutidas:

- **`key_id` desde o começo.** Sem ele, rotacionar a chave mestra vira uma
  migração de emergência: é preciso saber qual valor foi cifrado com qual chave
  para reescrever aos poucos. Custa uma coluna agora e evita um dia perdido
  depois.
- **`version` monotônica.** É o que permite ao sincronizador saber se o que está
  no Chatwoot é o que está aqui, sem comparar valores em claro.
- **Estado de sincronização em tabela própria.** Se ele fosse coluna do segredo,
  cada retentativa reescreveria a linha do segredo — e um erro de propagação
  ficaria indistinguível de uma edição.

## 4. Backend de segredos — a interface que torna a saída barata

O código nunca lê `os.environ` para um segredo de instalação. Ele pede ao
resolvedor:

```python
class SecretBackend(Protocol):
    def get(self, name: str) -> str | None: ...

# Ordem de resolução, e é ela que define o custo de trocar de nuvem:
#   1. DbBackend        — installation_secrets (o normal)
#   2. EnvBackend       — variável de ambiente (bootstrap e escape hatch)
#   3. InfisicalBackend — quando/se existir
```

A ordem com o banco primeiro é deliberada: enquanto env vencer, um valor velho
esquecido numa revisão do Cloud Run continua mandando, e ninguém entende por que
a chave nova "não pegou". O env fica como escape hatch consciente, não como
padrão silencioso.

Bootstrap continua em env porque não tem alternativa — e é exatamente por isso
que ele deve ser curto: dois nomes, não vinte.

## 5. Sincronização com o Chatwoot — o detalhe que decide tudo

Fato verificado na v3.16 (`lib/global_config_service.rb`): **a variável de
ambiente vence sobre o banco.** O `GlobalConfigService.load` faz
`ENV.fetch(key) { GlobalConfig.get(key) }`. E `FB_APP_ID`, `FB_APP_SECRET`,
`FB_VERIFY_TOKEN` e `IG_VERIFY_TOKEN` estão todos em
`config/installation_config.yml`, ou seja, são `InstallationConfig` — linhas de
banco, editáveis pelo Super Admin.

Duas consequências que mandam no desenho:

1. **É preciso PARAR de injetar `FB_*` como env no `deploy.sh`.** Enquanto a env
   existir, escrever no `installation_configs` não tem efeito nenhum — e o
   sintoma seria "salvei a chave na tela e o Instagram continua sumido".
2. Escrito no `installation_configs`, o valor passa a valer sem redeploy. É
   isto que elimina "criar env por ambiente".

O Chatwoot não expõe API para `InstallationConfig`. Duas formas de escrever:

| Via | Prós | Contras |
|---|---|---|
| **`rails runner` num Cloud Run Job** (recomendado) | Usa o modelo do Chatwoot, respeita `locked`, serialização correta | Precisa de um job por execução (~15 s) |
| Escrever direto na tabela | Instantâneo | O valor é YAML serializado de Ruby (`--- !ruby/hash:ActiveSupport::HashWithIndifferentAccess`); montar isso à mão é acoplar-se a um detalhe interno |

Fica o runner. O script vive no `chatwoot-rt` (`scripts/ops/sync_installation_config.rb`),
lê os valores de um endpoint autenticado do nosso backend e aplica:

```ruby
# Idempotente: só escreve o que mudou, e nunca toca config marcada `locked`.
faltando.each { |nome, valor| InstallationConfig.find_or_initialize_by(name: nome).update!(value: valor) }
GlobalConfig.clear_cache
```

### Quem dispara

- **No provisionamento** (`deploy.sh chatwoot`): passo do deploy, como o
  `locale_das_contas` que já existe.
- **Quando a chave é cadastrada depois**: o `POST /api/installation-secrets`
  marca `secret_sync_state` como `pending` e enfileira a propagação.
- **Retentativa imediata**: a própria propagação tenta 3 vezes com backoff curto
  (1 s, 4 s, 15 s) antes de desistir. É isto que absorve a falha momentânea —
  rollout do Cloud Run, job que demorou a subir, reset de conexão.
- **Reconciliador diário** (1x/dia): pega tudo que ficou `pending` ou `error` e
  tenta de novo. Decisão do dono, 03/08/2026: 15 min era ruído para um evento
  que acontece poucas vezes por instalação.

  A troca que isso implica, e que **não é opcional**: sendo diário, o
  reconciliador deixa de ser o mecanismo que revela o problema — passa a ser só
  o que conserta sozinho o que já é conhecido. Quem revela vira a tela. Então:

  - o `POST` responde `{status: "error", detail}` quando a propagação síncrona
    falha nas três tentativas — não `pending` otimista;
  - a listagem marca em vermelho todo segredo com `sync != ok`, e o painel
    mostra um aviso enquanto houver qualquer um nesse estado;
  - existe `POST /api/installation-secrets/{id}/sync` para o master forçar a
    propagação na hora, sem esperar o job.

  Sem esses três, diário reintroduz exatamente o modo de falha que o
  reconciliador existia para matar: silencioso, e só visível quando um cliente
  tenta conectar o Instagram.

### Segredo de canal (por inbox)

Não vai por `InstallationConfig`: vai na criação da inbox via Platform API do
Chatwoot, que a ponte já faz hoje para o W-API (`tenant_channels`, token
cifrado). O que muda é a origem do token: em vez de vir no corpo do request de
provisionamento, vem do nosso cofre.

## 6. Fluxo completo, ponta a ponta

```
Tela (master)                Backend                     Ponte / Chatwoot
    |                           |                                |
    |-- POST installation-      |                                |
    |   secrets {FB_APP_ID}     |                                |
    |                    cifra (Fernet, key_id)                  |
    |                    grava version=N                         |
    |                    sync_state = pending                    |
    |                           |-- enfileira propagação ------->|
    |                           |                        rails runner
    |                           |                        InstallationConfig
    |                           |<-- ok / erro + detalhe ---------|
    |<-- 201 {status: pending}  |                                |
    |                           |                                |
    |-- GET installation-       |                                |
    |   secrets                 |                                |
    |<-- [{name, version,       |   (valor NUNCA volta)          |
    |     sync: ok, ...}]       |                                |
```

## 7. Segurança — o que não pode afrouxar ao sair do Secret Manager

O Secret Manager dava três coisas de graça. Trocá-lo sem repô-las é regressão,
não simplificação:

| O que ele dava | Como se repõe aqui |
|---|---|
| Cifragem em repouso com chave gerenciada | Fernet com `ENCRYPTION_KEY` + cifragem em disco do Postgres |
| IAM por segredo | Permissão `secrets:*` já existente + **só master** para segredos de instalação |
| Auditoria de acesso | Linha em `audit_logs` a cada leitura *decifrada* e a cada escrita |

Regras que valem como requisito, não como recomendação:

- `GET` nunca devolve valor — nem para o master. A tela mostra nome, versão,
  data e estado de sincronização. Quem precisa do valor é o serviço, não a
  pessoa.
- Log nunca recebe valor, nem truncado. Sufixo de 4 caracteres, como
  `scripts/credencial_pix_qa.py` já faz, é o máximo.
- Rotação da chave mestra: duas chaves ativas (`k1` decifra, `k2` cifra),
  reescrita em lote, remoção da antiga. O `key_id` é o que torna isso possível
  sem parar o sistema.
- Backup do banco passa a conter todos os segredos cifrados. **O backup vira um
  ativo tão sensível quanto o cofre** — precisa da mesma restrição de acesso, e
  a chave mestra não pode estar no mesmo backup.

## 8. O que sobra em bootstrap (a meta de corte)

| Serviço | Depois desta spec |
|---|---|
| `teste-ia-backend` | `DATABASE_URL`, `ENCRYPTION_KEY` |
| `teste-ia-kernel` | `DATABASE_URL`, `ENCRYPTION_KEY` |
| `chatwoot-web`/`worker` | `DATABASE_URL` (Postgres), `SECRET_KEY_BASE`, `REDIS_URL` |
| `chatwoot-bridge` | `DATABASE_URL`, `ENCRYPTION_KEY` |

De 26 segredos no Secret Manager para 4 nomes distintos. `SERPER_API_KEY`,
`S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `BRIDGE_ADMIN_TOKEN`,
`CHATWOOT_PLATFORM_TOKEN`, `FB_*`, `IG_*` e a senha do super admin saem todos
para o banco.

O `SECRET_KEY_BASE` do Chatwoot não tem como sair: é ele que valida a sessão, e
é lido antes de qualquer coisa. O do Chatwoot é o caso mais teimoso justamente
por ser código de terceiro.

## 9. Fases

1. **Fundação** — tabela, `SecretBackend`, endpoints, tela. Nada muda em
   produção; o resolvedor cai em env como hoje.
2. **Instalação sai do env** — Serper, S3 e tokens de serviço passam a ser lidos
   pelo resolvedor. Deploy deixa de injetá-los. Um por vez, verificando.
3. **Chatwoot** — `sync_installation_config.rb`, remoção do `meta_secrets()` do
   `deploy.sh`, reconciliador. É aqui que Instagram e Messenger passam a ser
   ligados pela tela em vez de por `gcloud secrets create`.
4. **Canal** — token W-API e page tokens saem do corpo do provisionamento e
   passam a vir do cofre.
5. **Corte** — remover os segredos migrados do Secret Manager, deixando os de
   bootstrap. Só depois de uma rodada de QA completa.

Cada fase é reversível sozinha, e nenhuma exige a seguinte para valer a pena.

## 10. Como se prova que funciona

Um teste por afirmação que a spec faz:

- valor cifrado nunca volta pelo `GET`, nem para o master;
- segredo criado pela API chega ao `InstallationConfig` do Chatwoot **e** vale
  sem redeploy (subir o canal Meta depois de salvar a chave, sem tocar em env);
- com a env presente, o valor do banco é ignorado — teste que documenta a
  precedência do Chatwoot, para ninguém "consertar" isso por engano;
- propagação que falha deixa `status=error` com motivo **e o `POST` devolve esse
  erro ao chamador** — a tela nunca diz "salvo" para algo que não chegou;
- o reconciliador diário seguinte conserta um `error` sem intervenção;
- o sync manual (`POST .../{id}/sync`) propaga na hora, sem esperar o job;
- rotação de chave mestra: um valor cifrado com `k1` continua legível depois de
  `k2` virar a chave de escrita;
- log e resposta de erro não contêm o valor — teste que casa o corpo inteiro
  contra o segredo.

## 11. Riscos assumidos

- **Um banco comprometido entrega tudo.** Hoje um vazamento de banco entrega os
  segredos de tenant e não os de instalação; depois, entrega os dois. Mitiga-se
  com a chave mestra fora do banco e fora do backup — mas o risco aumenta, e
  isso é o preço de não depender do cofre da nuvem.
- **Ninguém rotaciona por nós.** O Secret Manager não rotacionava sozinho, mas
  dava versão e histórico. Aqui, versão é nossa responsabilidade.
- **A sincronização é eventual.** Entre salvar e valer no Chatwoot há um job. A
  tela precisa mostrar isso honestamente ("pendente"), em vez de dizer "salvo" e
  deixar o usuário achar que já vale.
- **Janela de conserto automático de até 24 h.** Com o reconciliador diário, um
  segredo que falhou nas três tentativas imediatas e cujo erro ninguém olhou na
  tela fica errado até o job do dia seguinte. É risco aceito conscientemente
  (03/08/2026), apoiado no fato de que cadastro de segredo de instalação é
  evento raro e sempre feito por um humano que está olhando o resultado — e
  mitigado pelo botão de sync manual. Se um dia a propagação passar a ser
  disparada por automação sem ninguém olhando, esta decisão precisa ser
  reaberta.
