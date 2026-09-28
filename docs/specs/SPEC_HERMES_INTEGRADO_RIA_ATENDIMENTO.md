# Especificação — Hermes integrado ao RIA Atendimento

**Status:** proposta de produto e arquitetura; sem implementação nesta etapa  
**Produto anfitrião:** Agent LLM / RIA Atendimento  
**Nome da capacidade:** Hermes by Rangel Tech  
**Data:** 28 de setembro de 2026

## 1. Decisão de produto

O Hermes não será uma plataforma web independente. Ele será uma superfície do RIA Atendimento, aproveitando o login, os tenants, as permissões, o histórico operacional, a observabilidade e a infraestrutura já existentes no Agent LLM.

O RIA terá dois modos claramente separados na mesma experiência:

1. **Conversas:** chat cotidiano e agentes do Kernel LLM. Um template simples pode ter um único agente e usar o Qwen como provider; templates futuros podem orquestrar vários agentes.
2. **Hermes agente:** inventário e controle das sessões Hermes abertas em extensões VS Code nos computadores autorizados do usuário.

Hermes e Kernel LLM não são a mesma coisa. O Hermes continua um agente de desenvolvimento com contrato direto com o Qwen/LLM; sua inferência não passa por Little LLM, LiteLLM/9Router, Kernel ou LangGraph. O Kernel continua sendo o motor de chat, templates e LangGraph do RIA. Eles compartilham identidade, auditoria e interface, não o ciclo de execução.

## 2. Objetivo

Permitir que um usuário autenticado no RIA Atendimento encontre e controle, sem QR code e sem uma nova aplicação, as sessões Hermes que estão rodando em seus notebooks e desktops autorizados.

Remote control, nesta especificação, significa controle real da sessão já aberta: o usuário seleciona um chat Hermes de um computador, escreve uma instrução no RIA, e essa instrução é entregue à extensão VS Code daquele computador. A extensão aciona o Hermes local como se a instrução tivesse sido enviada naquele ambiente; o resultado, eventos, solicitações de aprovação e estado retornam para o chat remoto no RIA.

O usuário deve poder entrar no RIA, selecionar **Hermes** no seletor de superfície e:

- ver os dispositivos conectados;
- ver todos os chats/sessões Hermes acessíveis por dispositivo e workspace, com as sessões ativas em destaque e histórico pesquisável sujeito à retenção;
- acompanhar eventos, respostas e estado da sessão em tempo real;
- enviar comandos e mensagens para a sessão correta;
- aprovar ou rejeitar solicitações que o Hermes declarar como interativas;
- retomar o trabalho em outro notebook por meio de contexto estruturado;
- auditar quem executou cada ação, de qual dispositivo e com qual resultado.

## 3. Fatos e ativos que serão reaproveitados

O desenho parte do Agent LLM atual, não de uma tela nova isolada:

- autenticação, usuário, tenant, papéis e API já existem no RIA Atendimento;
- a rota de chat e a interface de conversas já são maduras, com histórico, templates, streaming e painéis operacionais;
- o Kernel LLM já executa templates e agentes, usando LiteLLM para providers compatíveis com OpenAI;
- serviços de IA já armazenam credenciais cifradas e aceitam `openai-compatible` com `base_url`;
- a infraestrutura Rangel Tech já possui PostgreSQL, Redis, Traefik/TLS, observabilidade e deploy de infraestrutura na VPS;
- o Qwen é um provider OpenAI-compatible para o RIA e, separadamente, o modelo direto do Hermes.

Nada nesta especificação exige expor a API do Qwen ao navegador, reutilizar tokens OAuth de outros providers, ou colocar credenciais no código da extensão.

## 4. Arquitetura alvo

```text
                              RIA Atendimento
                         (uma aplicação, um login)
                                      |
               +----------------------+----------------------+
               |                                             |
               v                                             v
     Modo Conversas                               Modo Hermes agente
     /chat existente                              nova superfície da mesma SPA
               |                                             |
               v                                             v
     Backend -> Kernel LLM                         Backend -> ticket WebSocket curto
               |                                             |
               v                                             v
 LiteLLM / 9Router / Qwen                  Hermes Relay na VPS, via WSS/TLS
                                                        |
                                      Redis: presença e fan-out temporário
                                                        |
                                  Backend: persistência no Postgres/auditoria
                                                        |
                                                        v
                                         Extensão VS Code em conexão de saída
                                                        |
                                                        v
                                            Hermes local -> API Qwen direta
```

O **Hermes Relay** é um serviço técnico de comunicação em tempo real, não uma plataforma nem um segundo backend de produto. Ele existe para manter conexões WebSocket de longa duração com as extensões; o backend do RIA continua dono das regras de autorização, persistência e emissão de tickets. O Relay não grava estado de negócio diretamente no PostgreSQL: encaminha eventos autenticados ao backend, que persiste sessões, comandos, resultados e auditoria.

## 5. Limites de responsabilidade

| Componente | Responsabilidade | Não é responsabilidade |
|---|---|---|
| RIA frontend | escolha de modo, lista, visualização, comandos e feedback | executar comandos locais |
| Backend Agent LLM | autenticação, RBAC, tickets, regras de negócio, auditoria e APIs | manter socket de cada notebook |
| Kernel LLM | conversas, templates, LangGraph e agentes conversacionais | controlar sessão Hermes |
| Hermes Relay | presença, entrega WebSocket, fan-out e confirmação técnica | decidir se um usuário pode executar uma ação |
| Extensão Hermes | login, pareamento, gestão de múltiplos chats/sessões, publicação de eventos e execução de comandos locais autorizados | guardar credenciais de longa duração em texto |
| Hermes local | desenvolvimento e contrato direto com Qwen/LLM | passar por Little LLM, Kernel ou alterar dados do tenant sem comando recebido |
| Qwen | inferência | identidade, sessão web ou autorização |

## 6. Experiência de uso

### 6.1 Seletor global de superfície — referência ChatGPT/Codex

O padrão visual de referência é a alternância global **ChatGPT / Codex**: uma única escolha muda a natureza da área de trabalho, sem criar uma nova aplicação, um novo login ou uma navegação paralela. No RIA, a equivalência será **Conversas / Hermes agente**.

No topo da navegação lateral do RIA haverá um combo box compacto, antes da lista de conversas/projetos e visualmente distinto do seletor atual de template:

```text
[ Conversas v ]
  Conversas
  Hermes agente
```

O controle representa uma superfície persistente da conta, não o tipo de uma conversa. Deve manter a escolha ao navegar e restaurá-la na próxima abertura, respeitando a última superfície acessível ao usuário.

| Opção | Significado | Conteúdo principal |
|---|---|---|
| `Conversas` | IA conversacional cotidiana | chats, projetos, histórico, template e Kernel LLM |
| `Hermes agente` | central de agentes de desenvolvimento remotos | computadores, sessões VS Code, atividade e controles Hermes |

`Template: Padrão` permanece exclusivamente dentro de **Conversas**. Escolher `Hermes agente` não cria um chat normal, não altera um template e não mistura mensagens do Kernel com eventos do agente de desenvolvimento.

Rotas propostas:

```text
/chat                         Conversas existentes
/chat?surface=hermes           Hermes agente
/chat?surface=hermes&session=  detalhe de uma sessão Hermes
```

### 6.2 Navegação lateral no modo Hermes

Quando o usuário escolhe `Hermes agente`, a navegação lateral troca a árvore de chats pela árvore operacional Hermes. Cada computador/dispositivo é uma linha de primeiro nível; cada sessão VS Code disponível é filha desse computador. O equivalente ao marcador azul das referências será usado como sinal de disponibilidade e seleção, não como decoração.

```text
Hermes agente                                        [+ Conectar dispositivo]

Computadores
  Notebook pessoal                         ━━━ azul   conectado
    agent-platform / feature-hermes        ━━━ azul   executando
    extensão-vscode                                  ociosa
  Desktop trabalho                                    desconectado há 12 min
  Servidor de testes                       ━━━ azul   conectado
    qwen-ops / main                                  aguardando aprovação

Recentes
  sessão concluída ontem
  sessão de revisão de PR
```

Regras de comportamento:

- a linha do computador tem nome amigável, estado de conexão e total de sessões ativas; clicar expande/recolhe suas sessões;
- todo computador mostra um indicador de presença antes do nome: bolinha verde para conexão WSS saudável e bolinha vermelha para desconectado/indisponível; estados de reconexão usam cinza/âmbar, nunca verde;
- um traço azul de 3 px, discreto e com alto contraste, indica sessão disponível/conectada; o mesmo traço não deve fingir que uma sessão desconectada está disponível;
- a sessão selecionada usa fundo neutral sutil, tipografia com maior contraste e marcador azul de seleção; não usar cards coloridos ou alertas visuais excessivos;
- o estado `executando` pode ter ponto pulsante suave e `aguardando aprovação` usa badge âmbar contido; `erro` somente usa vermelho quando houver ação requerida;
- a lista é ordenada por disponibilidade e atividade recente, mas permite buscar, filtrar e paginar sessões históricas daquele computador; dispositivos revogados não aparecem como disponíveis;
- dispositivos sem sessões não desaparecem: podem ser expandidos, vistos e revogados, mas não receber comandos de sessão;
- em mobile/tablet, a árvore abre em drawer e a sessão selecionada ocupa a área principal.

### 6.3 Tela Hermes agente

A tela terá três áreas, preservando a linguagem visual premium do RIA:

```text
Dispositivos e sessões | Sessão selecionada                  | Estado e ações
-----------------------+------------------------------------+----------------------
Notebook pessoal       | eventos e conversa do Hermes       | conectado / ocioso
  projeto agente-llm   | diff, plano, resposta, aprovações  | workspace / branch
  projeto extensão     |                                    | enviar mensagem
Desktop trabalho       |                                    | parar / retomar
  projeto cliente      |                                    | aprovar / rejeitar
```

Estados obrigatórios: vazio, dispositivo sem sessão, desconectado, reconectando, sessão ociosa, executando, aguardando aprovação, concluída, falha e acesso negado.

Cada linha exibirá no mínimo: nome amigável do dispositivo, status, último contato, workspace, repositório quando disponível, branch, sessão, atividade atual e dono. A conversa selecionada deve carregar os eventos e mensagens já persistidos daquela sessão, permitindo que o usuário retome a leitura de qualquer chat Hermes acessível no notebook, não apenas o que está executando agora.

Ao selecionar uma sessão, a área principal deve se comportar como uma conversa remota do Hermes, não como uma mera tela de logs:

```text
Hermes / Notebook pessoal / agent-platform

Usuário no RIA: "Execute a revisão do módulo de autenticação e proponha o patch."
                    ↓ command_id criado e exibido como enviado
Hermes no VS Code: recebe a instrução e executa no workspace selecionado
                    ↓ eventos, respostas, diffs e pedido de aprovação
RIA: exibe progresso e a resposta na mesma conversa da sessão
```

O composer da conversa fica sempre vinculado ao `device_id` e `session_id` selecionados. Antes de enviar, mostra o destino em linguagem humana; se a bolinha estiver vermelha, o botão envia como fila com prazo ou bloqueia o envio, conforme a política de comando escolhida. Nunca pode redirecionar silenciosamente a instrução para outra máquina ou sessão.

### 6.4 Operações remotas

O primeiro corte deve suportar somente operações explicitamente modeladas:

- enviar mensagem/instrução para uma sessão;
- solicitar status e sincronizar eventos recentes;
- interromper uma execução cooperativa;
- retomar uma sessão;
- responder a uma solicitação de aprovação exposta pelo Hermes;
- abrir uma sessão em modo somente leitura;
- revogar um dispositivo.

Não haverá terminal arbitrário, shell remoto genérico, upload livre de chaves ou execução silenciosa de comandos fora do protocolo Hermes. Operações destrutivas ou que exijam confirmação no computador devem manter esse requisito no dispositivo e registrar a decisão.

#### Fluxo obrigatório de uma mensagem remota

1. o usuário abre um computador e seleciona um chat/sessão Hermes no RIA;
2. o RIA envia a instrução ao backend com o alvo explícito `tenant_id`, `device_id` e `session_id`;
3. o backend autoriza, grava um comando idempotente e o despacha ao Relay;
4. o Relay entrega o comando pela conexão WSS já aberta da extensão correta;
5. a extensão passa a instrução ao Hermes local no workspace daquela sessão;
6. o Hermes publica eventos e resposta pela extensão/Relay;
7. o backend persiste e o RIA atualiza a mesma conversa em tempo real.

Se a extensão rejeitar, estiver desconectada ou a sessão tiver terminado, o comando não pode ser executado em outro destino. O RIA exibe o estado final e uma ação explícita para reenviar, escolher outra sessão ou aguardar reconexão.

### 6.5 Diretriz obrigatória de frontend

Qualquer implementação desta interface deverá usar a skill **Frontend Premium** e começar pela inspeção dos componentes existentes do RIA. A referência ChatGPT/Codex serve para a hierarquia de informação e o modelo mental de alternar superfície; não autoriza copiar sua marca, CSS, ícones ou layout literalmente.

Requisitos de acabamento:

- reutilizar primeiro os componentes, tokens, navegação, padrões de loading e animações já existentes no frontend do Agent LLM;
- se não houver componente adequado, seguir a ordem: componente do projeto, shadcn/ui, bibliotecas aprovadas e somente então componente manual;
- interface SaaS operacional no nível Linear/Stripe/Vercel: tipografia legível, densidade controlada, espaço em múltiplos de 8, bordas suaves e sombras discretas;
- usar ícones Lucide e estados acessíveis de foco, teclado, carregamento, erro, vazio e indisponibilidade;
- microanimações curtas de `opacity`, `translateY` ou `scale` entre 200 e 350 ms; jamais animações decorativas que prejudiquem o acompanhamento de uma sessão;
- desktop primeiro, com árvore lateral em drawer no tablet/mobile; nenhum conteúdo operacional crítico pode depender de hover;
- validar visualmente estados reais da tela antes de concluir o frontend. Não é aceitável declarar a interface pronta apenas com componentes isolados ou mocks.

## 7. Identidade, pareamento e autorização

### 7.1 Login único

O usuário entra no RIA com a conta já existente. A tela Hermes usa a mesma sessão e o mesmo tenant. Não há cadastro paralelo, QR code ou senha específica do Hermes.

### 7.2 Pareamento da extensão

O primeiro pareamento será iniciado pela própria extensão no VS Code. Quando ainda não houver dispositivo autenticado, a extensão deve exibir uma tela nativa de boas-vindas/login com:

- email;
- senha;
- botão `Entrar no RIA Atendimento`;
- estado de carregamento, erro de credencial e indisponibilidade;
- link para criar/recuperar acesso no RIA, quando aplicável;
- identificação visual do tenant selecionado para confirmar que o login conecta aquele VS Code à conta/organização correta, sem fixar nome de tenant na extensão.

O formulário usa o endpoint de autenticação já existente do RIA por HTTPS/TLS. A senha serve apenas para autenticar a solicitação e nunca é persistida pela extensão, pelo Relay ou pelo banco de dispositivos. Após sucesso, o backend realiza o pareamento com autorização curta e vinculada ao tenant. Ao concluir:

1. o backend cria um registro de dispositivo;
2. emite uma credencial de dispositivo rotacionável;
3. a extensão guarda a credencial no armazenamento seguro do VS Code;
4. apenas o hash da credencial fica no banco;
5. a extensão abre conexão de saída WSS para o Hermes Relay.

Não reutilizar token OAuth de Claude Code, OpenAI, 9Router ou RAtende Connector como credencial Hermes. A extensão terá escopo próprio, revogável e auditável.

### 7.3 Página Hermes no RIA: download e gestão da extensão

O RIA Atendimento terá uma página de entrada para Hermes, acessível antes mesmo de existir uma sessão ativa. Ela deve ser a fonte oficial para instalar e administrar a extensão:

```text
Hermes by Rangel Tech

[ Baixar extensão para VS Code ]
versão atual | release notes | requisitos | checksum/assinatura

Meus dispositivos
Notebook pessoal       Conectado       Gerenciar
Desktop trabalho       Desconectado    Revogar

Como conectar
1. Instale a extensão
2. Abra Hermes no VS Code
3. Entre com seu email e senha do RIA
4. O dispositivo e suas sessões aparecem aqui
```

Requisitos da tela:

- download da versão publicada da extensão VSIX ou link controlado ao marketplace, com versão e notas de lançamento;
- instruções de instalação para VS Code e, se suportado, forks compatíveis;
- status de compatibilidade mínima da extensão e orientação de atualização;
- lista de dispositivos pareados, última atividade, revogação e rotação de acesso;
- nenhum token, senha ou segredo exibido/copied para o usuário;
- segregação integral por tenant e permissão.

### 7.4 Extensão VS Code: múltiplos chats e sessões Hermes

A extensão não é apenas um conector invisível. Ela deve oferecer no VS Code uma experiência Hermes mínima e funcional depois do login:

```text
Hermes by Rangel Tech

[ + Novo chat ]
                                            [ engrenagem: Configurações ]

Chats neste computador
  Revisar autenticação             executando
  Refatorar extensão               ocioso
  Investigar erro de deploy        concluído

Sessão selecionada
  conversa Hermes + composer + estado de execução
```

Requisitos obrigatórios:

- criar mais de um chat Hermes no mesmo computador;
- cada chat corresponde a uma `session_id` independente, com título, workspace, repositório/branch quando disponível, estado e histórico próprios;
- listar, selecionar, alternar, renomear, retomar, encerrar e arquivar chats sem interromper as demais sessões em execução;
- permitir que cada novo chat escolha ou confirme o workspace local antes de iniciar o Hermes;
- restaurar a lista de sessões do dispositivo após reiniciar o VS Code, respeitando o estado real do processo Hermes e o histórico persistido;
- publicar em tempo real ao RIA a criação, alteração de título, estado, eventos e término de todas as sessões, não apenas da que estiver ativa na interface local;
- quando o RIA enviar uma instrução, a extensão deve entregá-la exclusivamente à `session_id` indicada, mesmo que outro chat esteja aberto/focado no VS Code;
- manter a tela de login e o estado do dispositivo separados da lista de chats: fazer logout/revogar acesso encerra a conexão remota, mas não deve apagar silenciosamente o histórico local sem ação explícita do usuário.

### 7.5 Extensão VS Code: tela de configurações

Além das configurações convencionais disponíveis pelo VS Code e pelo `Ctrl+Shift+P`, a extensão deve ter uma tela própria de configurações, aberta pelo ícone de engrenagem no cabeçalho da interface Hermes. A engrenagem não pode ser apenas atalho para arquivos JSON: deve abrir uma tela legível e operacional, com labels, ajuda contextual, validação e estados de salvamento/erro.

```text
Hermes by Rangel Tech / Configurações

Conta e dispositivo
  conta/tenant ativo | nome deste computador | status | [Sair deste dispositivo]

Conexão remota
  URL do Relay | estado WSS | reconectar | diagnóstico de conectividade

LLM do Hermes
  Providers e modelos  >
  provider/modelo padrão | testar conexão

Comportamento
  workspace padrão | restaurar chats ao iniciar | notificações | tema

Privacidade e dados
  política de retenção local | limpar cache local | exportar diagnóstico sanitizado

[ Salvar alterações ]
```

#### Providers e modelos

`Providers e modelos` abre uma subpágina completa dentro das Configurações. A regra é: toda configuração de provider que o Hermes suportar deve ser realizável pela UI; arquivos JSON, variáveis de ambiente e `Ctrl+Shift+P` podem continuar existindo como alternativas técnicas, mas não podem ser o único caminho.

```text
Configurações / Providers e modelos

[ + Adicionar provider ]

Provider ativo                 Modelo padrão             Estado        Ações
Qwen direto                    qwen-abliterated          conectado     editar | testar | remover
Provider compatível OpenAI     modelo configurado         não testado   editar | testar | remover
...

Ao adicionar/editar
  tipo de provider [catálogo de providers suportados pelo Hermes]
  nome amigável
  endpoint/base URL quando aplicável
  método de autenticação compatível
  credencial segura
  modelo padrão e modelos disponíveis/configurados
  opções suportadas pelo provider (por exemplo: contexto, thinking, streaming)
  [Testar conexão] [Salvar]
```

Requisitos obrigatórios do catálogo:

- o catálogo é derivado das capacidades reais da versão instalada do Hermes; a UI não inventa providers nem esconde um provider suportado;
- permitir adicionar, editar, testar, definir como padrão, desabilitar e remover providers;
- permitir configurar mais de um provider e modelo, com padrão global do Hermes e escolha por novo chat; uma sessão já existente mantém seu provider/modelo salvo para ser reproduzível;
- mostrar somente opções que aquele provider/modelo suporta. Parâmetros não suportados devem ficar indisponíveis com explicação, em vez de serem silenciosamente ignorados;
- credenciais são gravadas exclusivamente no Secret Storage do VS Code. O formulário permite cadastrar, substituir, testar e remover, mas nunca recuperar ou exibir o valor já salvo;
- `Testar conexão` faz uma chamada mínima, informa latência/compatibilidade/erro sanitizado e não envia conteúdo de chats, arquivos ou comandos do usuário;
- configurações de providers pertencem ao perfil Hermes local do dispositivo. Não são publicadas como segredo para o RIA, Relay ou outros notebooks; somente metadados não sensíveis estritamente necessários podem aparecer no RIA;
- a UI deve explicar que esses providers são o caminho direto do Hermes para a LLM, independente de Little LLM, Kernel, 9Router e do canal de remote control.

Requisitos e limites:

- a tela de configurações é a superfície oficial para todas as configurações funcionais suportadas pelo Hermes: conta/dispositivo, conexão remota, providers/modelos, credenciais, comportamento de chats, workspaces, aprovações, notificações, privacidade/dados e diagnóstico;
- quando uma nova configuração funcional for adicionada ao Hermes, ela deve entrar na tela de configurações na mesma versão ou ter uma justificativa registrada no ADR; não é aceitável criar uma dependência permanente de JSON, shell ou Command Palette;
- a tela exibe o usuário/tenant autenticado e o `device_id` em formato seguro para diagnóstico, sem revelar credenciais;
- configurações do Relay e da LLM possuem validação antes de salvar e ação `Testar conexão`; resultados não podem registrar chaves, tokens ou headers;
- a configuração de providers/LLM deixa inequívoco que Hermes usa contrato direto com a LLM, separado de Little LLM, Kernel, 9Router e remote control;
- campos sensíveis usam o Secret Storage do VS Code; a tela apenas informa se uma credencial está configurada e oferece substituir/remover, nunca lê o segredo de volta;
- logout/revogação pede confirmação, remove a credencial de dispositivo do armazenamento seguro e fecha a conexão WSS, sem apagar chats/histórico local sem decisão explícita;
- `Ctrl+Shift+P` permanece como caminho alternativo para comandos rápidos, mas nenhuma função essencial de configuração depende exclusivamente dele;
- usar componentes existentes e a skill Frontend Premium: navegação por seções, foco de teclado, feedback de salvamento e layout responsivo dentro do painel VS Code.

### 7.6 RBAC mínimo

| Papel | Permissões Hermes |
|---|---|
| owner/admin do tenant | listar, controlar, aprovar, revogar e auditar todos os dispositivos do tenant |
| operador autorizado | controlar apenas sessões/dispositivos concedidos |
| visualizador | consultar sessões e eventos sem enviar comandos |
| extensão/dispositivo | publicar apenas sua própria presença, sessões e resultados; consumir comandos destinados a ele |

Toda solicitação do navegador será autorizada pelo backend antes de receber ticket para o Relay. O Relay valida assinatura, expiração, tenant, usuário, escopo e alvo da sessão.

### 7.7 Invariantes de multitenancy

O Hermes herda integralmente a multitenancy já existente no Agent LLM. O tenant não é apenas um filtro de interface: é parte obrigatória da identidade e da autorização de cada recurso e mensagem.

- todo dispositivo pertence a exatamente um `tenant_id` e só pode abrir WSS autenticado para esse tenant;
- toda sessão, evento, comando, aprovação, contexto e registro de auditoria contém `tenant_id` imutável e compatível com o dispositivo de origem;
- consultas do RIA começam pelo tenant da sessão autenticada; nunca aceitam `tenant_id` livre do navegador como única prova de acesso;
- o Relay separa presença, tópicos, conexões e fan-out por tenant e valida essa associação em cada ticket/comando;
- um usuário com acesso a mais de um tenant escolhe o tenant ativo antes de ver o modo Hermes; trocar de tenant troca integralmente a árvore de computadores, sessões e histórico;
- nenhuma mensagem, evento, credencial de dispositivo, contexto ou comando pode ser redespachado entre tenants, mesmo quando o mesmo usuário pertence aos dois;
- a validação cross-tenant é obrigatória nos testes de API, WSS, banco e interface.

## 8. Protocolo de conexão e confiabilidade

### 8.1 Canais

- **Extensão -> Relay:** WSS persistente de saída, com autenticação de dispositivo e reconexão com backoff.
- **RIA -> Backend:** HTTPS autenticado para leitura, comandos e auditoria.
- **RIA -> Relay:** WSS temporário emitido pelo backend para atualizações de tela; ou SSE no primeiro corte se simplificar o frontend.
- **Backend <-> Relay:** canal interno autenticado para presença e despacho, sem expor Redis ou Postgres publicamente.

### 8.2 Envelope de evento

Todos os eventos devem ter envelope versionado e idempotente:

```json
{
  "version": 1,
  "event_id": "uuid",
  "tenant_id": "uuid",
  "device_id": "uuid",
  "session_id": "uuid",
  "type": "hermes.session.updated",
  "occurred_at": "2026-09-28T00:00:00Z",
  "sequence": 42,
  "payload": {}
}
```

Tipos iniciais:

```text
device.hello
device.heartbeat
device.disconnected
hermes.session.upsert
hermes.session.event
hermes.approval.requested
hermes.command.queued
hermes.command.accepted
hermes.command.completed
hermes.command.rejected
hermes.command.failed
```

### 8.3 Comandos idempotentes

Cada comando recebe `command_id`, `idempotency_key`, alvo e prazo. Estados: `queued`, `delivered`, `accepted`, `running`, `completed`, `rejected`, `failed`, `cancelled` e `expired`.

O backend grava o comando antes de publicá-lo. O dispositivo confirma o recebimento e o resultado. Reconexão não deve repetir uma execução concluída; eventos duplicados são descartados por `event_id`/sequência.

## 9. Modelo de dados

O estado Hermes será separado das tabelas de conversas normais. Proposta de entidades:

| Entidade | Conteúdo principal |
|---|---|
| `hermes_devices` | tenant, usuário, nome, plataforma, versão da extensão, status, último contato, revogação |
| `hermes_device_credentials` | hash, escopo, emissão, expiração, rotação e revogação |
| `hermes_sessions` | dispositivo, workspace, repositório, branch, provider/modelo selecionados, estado, atividade, início/fim e metadados mínimos |
| `hermes_session_events` | sessão, sequência, tipo, payload sanitizado, horário e retenção |
| `hermes_commands` | emissor, destino, idempotência, estado, prazo, resultado e erro sanitizado |
| `hermes_approvals` | comando/evento associado, solicitante, decisão, justificativa e trilha de auditoria |
| `hermes_contexts` | resumo portátil da sessão e origem |
| `hermes_context_versions` | versões, diffs, autor, destino e data de aplicação |
| `hermes_audit_log` | ator, ação, recurso, resultado, IP de aplicação quando aplicável e correlação |

Índices devem priorizar `tenant_id`, `device_id`, `session_id`, `status`, `updated_at` e `occurred_at`. Payloads extensos terão limite e política de retenção; logs brutos, arquivos de repositório, tokens e cadeia de raciocínio não devem ser persistidos como evento de produto.

## 10. Contexto entre notebooks

Sincronizar o contexto bruto inteiro de uma sessão é caro, ruidoso e perigoso: carrega caminhos locais, saída de terminal, conteúdo sensível e histórico irrelevante. O produto deve sincronizar um **handoff estruturado**:

```text
objetivo atual
decisões tomadas
estado técnico
arquivos e caminhos relevantes
commits/branch/revisão
tarefas pendentes
riscos e bloqueios
próximo passo recomendado
```

Fluxo proposto:

1. a sessão de origem solicita ou produz um resumo Hermes versionado;
2. o usuário escolhe o dispositivo/sessão de destino;
3. o backend cria um contexto versionado e auditado;
4. a extensão de destino recebe uma proposta de importação;
5. o Hermes local injeta o resumo como contexto inicial somente após aceite.

Esse recurso entra depois do controle básico de sessões. Ele não é pré-requisito para o primeiro lançamento, mas é a forma correta de permitir continuação entre notebooks.

## 11. Uso do Qwen

### 11.1 Conversas do RIA

O Qwen será configurado como um serviço de IA `openai-compatible` do tenant. A URL base e a chave ficam apenas no backend, usando o mesmo mecanismo de cifragem dos demais serviços. Um template de conversa pessoal pode começar como supervisor único, sem agentes auxiliares, e futuramente evoluir para topologias multiagente do Kernel.

### 11.2 Hermes

O Hermes continuará chamando o Qwen/LLM diretamente para suas tarefas de desenvolvimento. Não há Little LLM, 9Router, LiteLLM, Kernel ou LangGraph no caminho de inferência do Hermes. A sessão Hermes não se torna uma conversa normal do RIA: o RIA somente autentica o usuário, autoriza o acesso remoto, controla a sessão e visualiza seus eventos; não concorre com o agente por sua memória de execução.

O contrato direto precisa ter configuração explícita e independente do remote control:

- a extensão/Hermes recebe a URL da LLM e a credencial de inferência por configuração própria do Hermes;
- a credencial fica somente no armazenamento seguro local do VS Code ou é um token de inferência de curta duração obtido pelo Hermes; ela nunca transita pelo browser do RIA, Relay ou histórico de eventos;
- a escolha entre chave local persistente e token de curta duração deve ser tomada no ADR de implementação, avaliando disponibilidade da API Qwen e custo operacional;
- qualquer credencial desse fluxo autoriza apenas inferência contra a LLM, jamais comandos Hermes, leitura de sessões de outros dispositivos ou administração do RIA.

### 11.3 Propriedade de dados

| Dado | Dono |
|---|---|
| conversa pessoal, template, memória e execução de agente | Kernel LLM / RIA |
| sessão de desenvolvimento, eventos e aprovações | Hermes |
| inferência | Qwen |
| identidade, tenant, RBAC e auditoria | Agent LLM backend |

## 12. Serviços e implantação

O produto continua hospedado no ecossistema Rangel Tech existente. A implantação adicionará, quando a fase de implementação for autorizada, o serviço `hermes-relay` atrás do Traefik/TLS na VPS. PostgreSQL será a fonte de verdade; Redis será transitório para presença, fan-out e coordenação curta.

O Relay não deve depender de uma conexão de longa duração em Cloud Run. O backend pode continuar onde já está; a conexão contínua dos notebooks deve terminar em infraestrutura que suporte WebSocket persistente e observabilidade direta.

Requisitos operacionais:

- healthcheck, readiness e métricas de conexões, reconexões, latência e comandos pendentes;
- logs estruturados com `tenant_id`, `device_id`, `session_id` e `command_id`, sem segredos;
- alertas para Relay indisponível, muitos dispositivos desconectados e fila de comandos envelhecida;
- backup/migração das novas tabelas;
- política de retenção para eventos e auditoria;
- rate limit por tenant, usuário e dispositivo.

## 13. Fases de entrega

### Fase A — fundação de domínio

- migrations e modelos Hermes;
- APIs de dispositivos, sessões, eventos e auditoria;
- RBAC e credencial própria de dispositivo;
- contratos versionados e testes de autorização/idempotência.

### Fase B — Relay e extensão

- serviço WSS `hermes-relay` na VPS;
- pareamento seguro da extensão;
- interface da extensão para login e múltiplos chats/sessões Hermes por computador;
- tela própria de configurações da extensão, acessível por engrenagem;
- catálogo visual de todos os providers/modelos e opções realmente suportados pelo Hermes;
- presença, heartbeat, reconexão e publicação de sessões;
- fila de comandos e confirmação de entrega.

### Fase C — interface RIA

- combo box global Conversas/Hermes agente, inspirado no modelo mental ChatGPT/Codex;
- árvore lateral de computadores e sessões, com marcador azul de disponibilidade e seleção;
- lista de dispositivos e sessões;
- viewer de eventos, status e comandos básicos;
- estados vazios, erro, desconexão e permissões.

### Fase D — controle completo e auditoria

- aprovações remotas declaradas pelo Hermes;
- parar/retomar, mensagens e resultado de comando;
- trilha de auditoria pesquisável;
- telemetria, alertas e runbook operacional.

### Fase E — continuidade entre máquinas

- contexto Hermes estruturado e versionado;
- exportar/importar com aceite explícito;
- comparação de contexto e histórico de handoffs.

## 14. Critérios de aceite

O primeiro lançamento só será considerado pronto quando for demonstrado, com duas máquinas reais:

1. o mesmo usuário do RIA pareia duas extensões sem QR code;
2. a extensão apresenta login de email e senha do RIA dentro do VS Code e não persiste a senha após autenticar;
3. a mesma extensão cria e mantém pelo menos duas sessões Hermes independentes, em workspaces ou chats distintos, sem cruzar mensagens ou estado;
4. a engrenagem abre uma tela de configurações operacional, com teste de conexão e sem depender de `Ctrl+Shift+P`;
5. todos os providers e opções realmente suportados pela versão do Hermes são configuráveis pela UI, incluindo adicionar, testar, selecionar modelo, definir padrão e remover provider sem expor segredo;
6. a página Hermes do RIA disponibiliza a extensão publicada e suas instruções de instalação;
7. o combo box troca efetivamente entre Conversas e Hermes agente, sem alterar template ou misturar históricos;
8. ambas aparecem no modo Hermes, segregadas por tenant e permissões;
9. cada sessão aberta no VS Code surge em tempo real na árvore correta, com traço azul e bolinha verde/vermelha fiéis à disponibilidade;
10. uma mensagem enviada pelo RIA chega exclusivamente à sessão selecionada, é executada pelo Hermes no VS Code de destino e aparece como resposta/evento na mesma conversa remota;
11. o resultado retorna ao RIA com correlação de comando;
12. queda e reconexão da internet não duplicam comando já concluído;
13. revogar um dispositivo encerra seu acesso e impede nova conexão;
14. uma conversa normal com template Qwen continua funcionando sem alteração de comportamento;
15. auditoria mostra autor, dispositivo, sessão, comando e resultado;
16. os testes não expõem chaves, tokens, conteúdo bruto do terminal ou arquivos locais indevidos.

## 15. Fora de escopo nesta etapa

- nova plataforma web, novo login ou novo domínio de produto;
- terminal remoto genérico e shell arbitrário pelo navegador;
- copiar todo o histórico bruto/context window entre notebooks;
- tornar o Hermes um agente interno do LangGraph;
- reusar credenciais OAuth de outros providers;
- mudanças na infraestrutura de produção antes de aprovação da implementação;
- alterar o runtime, disco ou configuração operacional da VM do Qwen como parte desta integração.

## 16. Decisões ainda necessárias antes da implementação

Não são bloqueios conceituais; são escolhas que precisam ser fechadas no ADR e nos contratos técnicos antes de abrir as tarefas de código:

1. **Credencial direta Hermes → Qwen:** chave local no armazenamento seguro do VS Code ou token de inferência de curta duração emitido para o Hermes.
2. **Autenticação na extensão:** confirmar se email/senha é suficiente para todas as contas RIA ou se o fluxo deve respeitar MFA/SSO quando esses métodos forem habilitados.
3. **Escopo de compartilhamento:** no primeiro lançamento, definir se um operador pode receber acesso a um dispositivo inteiro ou somente a sessões explicitamente compartilhadas pelo dono.
4. **Retenção:** definir dias de retenção para eventos de sessão, auditoria, contextos estruturados e comandos concluídos, além de limites de payload.
5. **Distribuição:** decidir se a versão inicial será VSIX privado pelo RIA, Marketplace privado ou ambos, e como ocorrerá atualização obrigatória de extensões incompatíveis.

## 17. Entregáveis para iniciar implementação

1. ADR validando esta separação entre Conversas, Hermes e Qwen.
2. Contrato OpenAPI/JSON Schema dos endpoints e envelopes WebSocket.
3. Diagrama de sequência de pareamento, comando, reconexão e revogação.
4. Modelo de dados e migrations revisados.
5. Protótipo visual das superfícies Conversas e Hermes no padrão atual do RIA.
6. Plano de rollout, observabilidade, retenção e rollback do Relay.
7. Matriz de ameaças e permissões, com foco em device token, cross-tenant e comandos repetidos.

## 18. Decisões explícitas

- **Uma experiência web:** Hermes entra no RIA Atendimento; não haverá portal paralelo.
- **Dois planos de execução:** Kernel/Little LLM para conversas; Hermes com contrato direto com a LLM para desenvolvimento.
- **Uma identidade:** login, tenant e papéis do Agent LLM são a fonte de autorização.
- **Relay técnico dedicado:** necessário para conexões persistentes das extensões, sem duplicar produto.
- **Qwen como provider, não como banco de estado:** o modelo infere; o RIA e Hermes mantêm seus próprios registros.
- **Contexto portátil, não cópia bruta:** resumos estruturados evitam custo, ruído e vazamento desnecessário.
- **Segurança proporcional e operacional:** tokens próprios, rotação, revogação, auditoria e autorização por alvo são requisitos para evitar que uma sessão ou notebook controle outro indevidamente.
