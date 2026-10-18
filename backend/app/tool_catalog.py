"""Catálogo das ferramentas que o manifesto de um agente por skills pode listar.

Só rótulos: o catálogo diz o que existe e se a ferramenta exige confirmação;
a execução e as credenciais ficam fora daqui. Os ids são estáveis (o manifesto
os grava); os rótulos e descrições são o que o cliente lê.
"""

TOOLS: list[dict] = [
    {
        "id": "rt_data_describe",
        "label": "Conhecer as fontes de dados",
        "description": "Lista as fontes de dados da empresa e suas tabelas.",
        "sensitive": False,
    },
    {
        "id": "rt_sql_read",
        "label": "Consultar dados (leitura)",
        "description": "Consulta tabelas das fontes de dados da empresa, sem alterar nada.",
        "sensitive": False,
    },
    {
        "id": "rt_sql_write",
        "label": "Alterar dados",
        "description": (
            "Altera registros nas tabelas liberadas. Pede confirmação antes de cada alteração."
        ),
        "sensitive": True,
    },
    {
        "id": "rt_mongo_find",
        "label": "Consultar coleções",
        "description": "Busca documentos em fontes de dados de documentos.",
        "sensitive": False,
    },
    {
        "id": "rt_pix_create",
        "label": "Criar cobrança Pix",
        "description": "Gera uma cobrança Pix com QR Code. Pede confirmação antes de criar.",
        "sensitive": True,
    },
    {
        "id": "rt_payment_status",
        "label": "Consultar pagamento",
        "description": "Consulta a situação de uma cobrança.",
        "sensitive": False,
    },
    {
        "id": "rt_email_list",
        "label": "Ler e-mails",
        "description": "Lista mensagens das caixas de e-mail da empresa.",
        "sensitive": False,
    },
    {
        "id": "rt_email_send",
        "label": "Enviar e-mail",
        "description": "Envia e-mails pelas caixas da empresa. Pede confirmação antes de enviar.",
        "sensitive": True,
    },
    {
        "id": "rt_google_calendar",
        "label": "Agenda do Google",
        "description": "Consulta e cria compromissos na agenda conectada.",
        "sensitive": True,
    },
    {
        "id": "rt_google_sheets",
        "label": "Planilhas do Google",
        "description": "Lê e escreve em planilhas conectadas.",
        "sensitive": True,
    },
    {
        "id": "rt_ms_calendar",
        "label": "Agenda do Outlook",
        "description": "Consulta e cria compromissos na agenda do Outlook conectada.",
        "sensitive": True,
    },
    {
        "id": "rt_http",
        "label": "Chamar serviços externos",
        "description": "Faz chamadas a serviços de terceiros com as chaves cadastradas na empresa.",
        "sensitive": True,
    },
    {
        "id": "rt_search_docs",
        "label": "Buscar nos arquivos da empresa",
        "description": "Procura trechos nos arquivos enviados à empresa.",
        "sensitive": False,
    },
    {
        "id": "rt_memory_recall",
        "label": "Lembrar do usuário",
        "description": "Consulta o que a plataforma já aprendeu sobre o usuário.",
        "sensitive": False,
    },
    {
        "id": "rt_artifact_put",
        "label": "Entregar arquivos e gráficos",
        "description": "Registra arquivos, imagens e tabelas gerados para o usuário baixar.",
        "sensitive": False,
    },
    {
        "id": "rt_executor_run",
        "label": "Rodar código e gerar gráficos",
        "description": (
            "Executa cálculos e gera gráficos, planilhas e PDFs num ambiente isolado, sem internet."
        ),
        "sensitive": False,
    },
    {
        "id": "rt_image_generate",
        "label": "Gerar imagens",
        "description": "Cria imagens a partir de uma descrição.",
        "sensitive": False,
    },
    {
        "id": "rt_template_plan",
        "label": "Montar agentes conversando",
        "description": "Propõe e cria novos agentes a partir de uma conversa, após confirmação.",
        "sensitive": True,
    },
    {
        "id": "rt_env_status",
        "label": "Ver o ambiente da empresa",
        "description": "Consulta o estado do ambiente e dos aplicativos publicados.",
        "sensitive": False,
    },
    {
        "id": "rt_env_manage",
        "label": "Operar o ambiente da empresa",
        "description": (
            "Inicia o ambiente, publica aplicativos e configura domínios. Pede confirmação."
        ),
        "sensitive": True,
    },
    {
        "id": "rt_job_schedule",
        "label": "Tarefas longas e agendadas",
        "description": "Agenda tarefas e roda trabalhos demorados em segundo plano.",
        "sensitive": True,
    },
]


def catalog() -> list[dict]:
    return [dict(t) for t in TOOLS]


def tool_ids() -> set[str]:
    return {t["id"] for t in TOOLS}


# Ferramenta -> tipo de credencial que ela usa. O agente só enxerga credenciais dos tipos das
# ferramentas que o manifesto liga (princípio do menor privilégio; spec 04 §6).
_CREDENTIAL_KIND = {
    "rt_data_describe": "datasource",
    "rt_sql_read": "datasource",
    "rt_sql_write": "datasource",
    "rt_sql_transaction": "datasource",
    "rt_mongo_find": "datasource",
    "rt_pix_create": "mercado_pago",
    "rt_payment_status": "mercado_pago",
    "rt_mp_read": "mercado_pago",
    "rt_email_list": "email",
    "rt_email_send": "email",
    "rt_http": "secrets",
}


def credential_kinds(tools: list[str]) -> list[str]:
    """Tipos de credencial implicados pelas ferramentas do manifesto (sem repetir)."""
    out: list[str] = []
    for t in tools:
        kind = _CREDENTIAL_KIND.get(t)
        if kind and kind not in out:
            out.append(kind)
    return out
