"""Catálogo curado de provedores de IA.

A instância de 9Router conhece 80 provedores e 752 modelos. Jogar isso cru na
tela do cliente seria transferir para ele um problema que é nosso: a maioria
são serviços regionais, de mídia ou de nicho que ninguém desta plataforma vai
conectar. Aqui fica a lista que a UI oferece, com rótulo em português e o modo
de conexão de cada um.

Os três fatos que este arquivo carrega, todos verificados na instância real:

1. **O prefixo do modelo não é o nome do provedor da conta.** Uma conta
   `claude` (assinatura) serve modelos `cc/…`; uma conta `codex` serve `cx/…`.
   Sem esse mapa não dá para dizer quais modelos as contas do tenant liberam —
   e `/v1/models` devolve o catálogo inteiro, não o que está conectado.
2. **Cada provedor OAuth tem um fluxo diferente.** Uns redirecionam e devolvem
   um código para colar (`redirect`), outros mostram um código para digitar no
   site e ficam sendo consultados (`device`).
3. **`cursor` está quebrado** na versão 0.5.40 (`d.buildAuthUrl is not a
   function`), então ele fica de fora até voltar a funcionar.
"""

# Modo de conexão:
#   apikey   — o cliente cola a chave dele
#   redirect — abre a tela do provedor, volta com um código para colar
#   device   — mostra um código para digitar no site do provedor; a instância
#              fica consultando até o cliente confirmar
PROVEDORES: list[dict] = [
    # -- Assinaturas (o caso que motivou tudo isso) ------------------------
    {
        "id": "claude",
        "nome": "Claude (assinatura)",
        "modo": "redirect",
        "prefixo": "cc",
        "nota": "Usa a assinatura Claude que a empresa já paga.",
    },
    {
        "id": "codex",
        "nome": "ChatGPT / Codex (assinatura)",
        "modo": "redirect",
        "prefixo": "cx",
        "nota": "Usa a assinatura ChatGPT Plus/Pro da empresa.",
    },
    {
        "id": "github",
        "nome": "GitHub Copilot",
        "modo": "device",
        "prefixo": "gh",
        "nota": "Usa a assinatura Copilot da empresa.",
    },
    {
        "id": "antigravity",
        "nome": "Antigravity (Google)",
        "modo": "redirect",
        "prefixo": "ag",
    },
    {"id": "gemini-cli", "nome": "Gemini CLI (conta Google)", "modo": "redirect", "prefixo": "gc"},
    {"id": "qwen", "nome": "Qwen Code", "modo": "device", "prefixo": "qw"},
    {"id": "kimi", "nome": "Kimi", "modo": "device", "prefixo": "kimi"},
    {"id": "kilocode", "nome": "Kilo Code", "modo": "device", "prefixo": "kc"},
    {"id": "cline", "nome": "Cline", "modo": "redirect", "prefixo": "cl"},
    # -- Chave de API -----------------------------------------------------
    {"id": "gemini", "nome": "Google Gemini", "modo": "apikey", "prefixo": "gemini"},
    {"id": "openai", "nome": "OpenAI", "modo": "apikey", "prefixo": "openai"},
    {"id": "anthropic", "nome": "Anthropic", "modo": "apikey", "prefixo": "anthropic"},
    {"id": "deepseek", "nome": "DeepSeek", "modo": "apikey", "prefixo": "deepseek"},
    {"id": "groq", "nome": "Groq", "modo": "apikey", "prefixo": "groq"},
    {"id": "xai", "nome": "xAI (Grok)", "modo": "apikey", "prefixo": "xai"},
    {"id": "openrouter", "nome": "OpenRouter", "modo": "apikey", "prefixo": "openrouter"},
    {"id": "mistral", "nome": "Mistral", "modo": "apikey", "prefixo": "mistral"},
    {"id": "cerebras", "nome": "Cerebras", "modo": "apikey", "prefixo": "cerebras"},
    {"id": "together", "nome": "Together AI", "modo": "apikey", "prefixo": "together"},
    {"id": "fireworks", "nome": "Fireworks AI", "modo": "apikey", "prefixo": "fireworks"},
    {"id": "perplexity", "nome": "Perplexity", "modo": "apikey", "prefixo": "perplexity"},
]

POR_ID = {p["id"]: p for p in PROVEDORES}

# Prefixo de modelo -> id do provedor da conta. Usado para responder "quais
# modelos as contas desta empresa liberam".
PREFIXO_PARA_PROVEDOR = {p["prefixo"]: p["id"] for p in PROVEDORES}


def provedor_de_modelo(model_id: str) -> str | None:
    """`cc/claude-sonnet-5` -> `claude`. Devolve None para prefixo fora do
    catálogo — modelo que não sabemos ligar a uma conta não entra em combo."""
    prefixo = model_id.split("/", 1)[0]
    return PREFIXO_PARA_PROVEDOR.get(prefixo)
