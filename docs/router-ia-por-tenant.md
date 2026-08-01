# Camada de modelos por tenant (9Router)

## O problema que essa camada resolve

O cliente quer usar **as contas dele** — inclusive a assinatura pessoal do
Claude que ele já paga — e revezar entre várias contas para não bater no limite
de nenhuma. Nem BYOK direto nem LiteLLM sozinho fazem isso: assinatura pessoal
não expõe API key, e revezamento entre contas não existe no cliente do modelo.

## A decisão que carrega o resto: uma instância por tenant

Foi verificado na instância real, não suposto:

```sql
CREATE TABLE apiKeys (id, key, name, machineId, isActive, createdAt);   -- sem dono
CREATE TABLE combos  (id, name, kind, models, ...);                     -- global
CREATE INDEX idx_pc_provider_active ON providerConnections(provider, isActive);
CREATE INDEX idx_pc_priority        ON providerConnections(provider, priority);
```

Um combo lista **modelos** (`["cc/claude-sonnet-5", "gemini/..."]`), nunca
contas. A conta que atende é escolhida por `(provider, isActive, priority)`
dentro do 9Router.

Consequência: **numa instância compartilhada, a conta OAuth do cliente A pode
atender a chamada do cliente B** — e nenhuma regra da plataforma impede, porque
a escolha acontece lá dentro, depois que a requisição já saiu daqui.

Por isso cada empresa tem a própria instância. O isolamento vira propriedade do
deploy (processo e volume separados), não disciplina de código. Menos código
para errar, e a garantia deixa de ser promessa.

### O custo disso, medido

Laboratório com uma instância carregada muito além do uso real de um tenant:

| Medida | Valor |
|---|---|
| 500 chaves criadas | 18 s |
| 200 combos criados | 7 s |
| Listar combos / chaves | 20 ms / 31 ms |
| `GET /v1/models` | 33 ms |
| Memória do container | 105 MB |
| Banco SQLite | 4,4 MB |

Escala não é o problema. Uma instância de tenant real tem poucas contas e
poucos combos. Na VPS atual (6 vCPU, 12 GB, ~2 GB em uso), **10 empresas ≈ 1 GB**.
Acima de ~40 instâncias, vale outra máquina ou limite de memória por container.

## Como as peças se ligam

```
Serviços de IA (nossa UI)
   ├─ Contas   ──POST /api/ai-router/contas──▶ instância do tenant  (/api/providers)
   └─ Combos   ──POST /api/ai-router/combos──▶ instância do tenant  (/api/combos)
                        │
                        └─ publica um ai_service (openai-compatible)
                                      │
                          o editor de template não muda nada:
                          o combo aparece como mais um serviço de IA
                                      │
                                   kernel ──▶ https://ia-<tenant>.rangeltech.net/v1
```

O combo virar `ai_service` é o que mantém isso simples: template, kernel e
editor seguem iguais. O que muda é só de onde vem o modelo.

## Isolamento — o que garante o quê

| Garantia | Como |
|---|---|
| Uma empresa não usa a conta de outra | Instância dedicada: a conta nem existe na instância alheia |
| Um template não usa combo de outra empresa | `ai_service` é por tenant; o editor só lista os do próprio |
| Um combo não usa modelo de fora | Validado contra `/v1/models` da instância **do tenant** antes de criar |
| Credencial não vaza | Senha e chave da instância cifradas (Fernet) e nunca retornadas |
| Cliente não alcança instância alheia | A instância é resolvida pelo tenant da **sessão**, nunca por id do cliente |

Coberto por `backend/tests/test_ai_router.py`.

## Operação

### Provisionar uma empresa

```bash
python scripts/provisionar_router.py <tenant_key>
```

Cria container e volume, define senha e chave no primeiro boot
(`INITIAL_PASSWORD` / `ROUTER_API_KEY`), publica no Traefik como
`ia-<tenant_key>.rangeltech.net` com TLS, e registra na plataforma.

O registro DNS é criado pelo próprio script quando `HOSTINGER_API_KEY` está no
ambiente. O certificado sai alguns minutos depois: o Let's Encrypt precisa
enxergar o registro, e o Traefik tenta de novo sozinho. Se a instância demorar
a responder em HTTPS, `docker restart traefik` força uma nova tentativa.

### Remover

```bash
python scripts/provisionar_router.py <tenant_key> --remover
```

Remove o container e preserva o volume em `/opt/platform/data/`.

### Contas por assinatura (OAuth)

O consentimento tem que acontecer na tela do serviço que vai guardar a sessão.
Como a instância é do tenant, o caminho é: abrir
`https://ia-<tenant>.rangeltech.net`, conectar a conta por lá, e clicar em
**Sincronizar contas** na plataforma. A partir daí ela aparece na lista e pode
entrar em combos.

## Validado em produção

Com o tenant `loja-demo`:

1. `provisionar_router.py loja-demo` criou container, volume, DNS e TLS;
2. a chave de consumo foi criada pela API administrativa da instância — a
   variável `ROUTER_API_KEY` **não** registra chave utilizável em `/v1`, ela
   alimenta um subprocesso interno do 9Router;
3. a conta Gemini da loja foi conectada pela tela da plataforma;
4. o combo `Producao` (dois modelos revezando) virou o serviço de IA
   `Combo: Producao`;
5. um template apontando para esse combo respondeu, consultando o banco da
   loja: *"A Furadeira 650W custa R$ 289,90 e possui 15 unidades em estoque."*

## O que ainda não está pronto

- **Autosserviço de provisionamento**: hoje é script rodado pelo dono. Um
  serviço na VPS que aceite o pedido da plataforma resolveria — está fora deste
  corte de propósito, para não inventar um canal de execução remota agora.
- **Fallback automático**: se a instância cair, o agente falha. O caminho certo
  é o template ter um serviço BYOK de reserva; hoje isso é escolha manual.
- **Painel de consumo**: `GET /api/ai-router/uso` já devolve o dado da
  instância (já filtrado por tenant, porque a instância é dele); falta a tela.
