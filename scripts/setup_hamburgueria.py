"""Bootstrap idempotente do cenário Hamburgueria Demo.

Cria/atualiza o tenant, datasource PostgreSQL dedicado, schema/tabelas da
hamburgueria, cardápio de exemplo e dois templates:
- Atendimento Hamburgueria
- Admin Hamburgueria

Pré-requisitos esperados no ambiente:
- backend acessível via HOMOLOG_BACKEND
- credenciais master válidas
- pelo menos uma chave de provedor configurada (Gemini/OpenAI/Anthropic)
- Postgres da demo acessível via HOMOLOG_PG_*

Seguro para reexecução: reusa tenant/AI services/datasource/template existentes
 e faz upsert dos produtos pelo nome.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any

import psycopg

from setup_homolog import (
    ADMIN_PW,
    BACKEND,
    DEFAULT_PROVIDER,
    MASTER,
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
    admin_token,
    ensure_datasource,
    ensure_provider_services,
    ensure_template,
    ensure_tenant,
)

TENANT_NAME = "Hamburgueria Demo"
TENANT_KEY = "hamburgueria-demo"
TENANT_ADMIN_EMAIL = "dono@hamburgueriademo.com"
DATASOURCE_NAME = "hamburgueria"
SCHEMA = os.environ.get("HAMBURGUERIA_SCHEMA", "hamburgueria")
DATASOURCE_HOST = os.environ.get(
    "HAMBURGUERIA_DATASOURCE_HOST",
    "postgres" if POSTGRES_HOST in {"127.0.0.1", "localhost"} else POSTGRES_HOST,
).strip()
DATASOURCE_PORT = int(
    os.environ.get(
        "HAMBURGUERIA_DATASOURCE_PORT",
        "5432" if POSTGRES_HOST in {"127.0.0.1", "localhost"} and POSTGRES_PORT == 5433 else str(POSTGRES_PORT),
    ).strip()
)

MENU: list[dict[str, Any]] = [
    {
        "name": "Burger Clássico",
        "description": "Pão brioche, hambúrguer 160g, queijo, alface e tomate.",
        "category": "lanches",
        "price": Decimal("29.90"),
    },
    {
        "name": "Burger Bacon",
        "description": "Hambúrguer 160g, bacon crocante, cheddar e maionese da casa.",
        "category": "lanches",
        "price": Decimal("34.90"),
    },
    {
        "name": "Burger Duplo Smash",
        "description": "Dois discos smash, queijo prato, cebola roxa e picles.",
        "category": "lanches",
        "price": Decimal("37.50"),
    },
    {
        "name": "Chicken Crispy",
        "description": "Frango crocante, alface americana e molho especial.",
        "category": "lanches",
        "price": Decimal("31.00"),
    },
    {
        "name": "Veggie Burger",
        "description": "Hambúrguer vegetal, queijo, rúcula e tomate confit.",
        "category": "lanches",
        "price": Decimal("32.50"),
    },
    {
        "name": "Batata Frita P",
        "description": "Porção individual de batata frita crocante.",
        "category": "acompanhamentos",
        "price": Decimal("12.00"),
    },
    {
        "name": "Batata Frita G",
        "description": "Porção grande para compartilhar.",
        "category": "acompanhamentos",
        "price": Decimal("18.00"),
    },
    {
        "name": "Onion Rings",
        "description": "Anéis de cebola empanados com molho barbecue.",
        "category": "acompanhamentos",
        "price": Decimal("16.50"),
    },
    {
        "name": "Refrigerante Lata",
        "description": "Coca-Cola, Guaraná ou Sprite 350ml.",
        "category": "bebidas",
        "price": Decimal("7.00"),
    },
    {
        "name": "Suco Artesanal",
        "description": "Limonada, maracujá ou morango 400ml.",
        "category": "bebidas",
        "price": Decimal("9.50"),
    },
    {
        "name": "Milkshake Chocolate",
        "description": "Milkshake cremoso 400ml.",
        "category": "sobremesas",
        "price": Decimal("18.90"),
    },
    {
        "name": "Cookie com Sorvete",
        "description": "Cookie quente com bola de sorvete de creme.",
        "category": "sobremesas",
        "price": Decimal("19.90"),
    },
]


def pg_conninfo() -> str:
    if not POSTGRES_PASSWORD:
        raise RuntimeError("HOMOLOG_PG_PASSWORD é obrigatório para o cenário hamburgueria")
    return (
        f"host={POSTGRES_HOST} port={POSTGRES_PORT} dbname={POSTGRES_DB} "
        f"user={POSTGRES_USER} password={POSTGRES_PASSWORD}"
    )


def quoted_schema() -> str:
    return SCHEMA.replace('"', '""')


def prepare_database() -> None:
    schema = quoted_schema()
    with psycopg.connect(pg_conninfo()) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            cur.execute(
                f'''
                CREATE TABLE IF NOT EXISTS "{schema}".produtos (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    nome TEXT NOT NULL,
                    descricao TEXT NOT NULL DEFAULT '',
                    preco NUMERIC(10,2) NOT NULL CHECK (preco >= 0),
                    categoria TEXT NOT NULL,
                    ativo BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (nome)
                )
                '''
            )
            cur.execute(
                f'''
                CREATE TABLE IF NOT EXISTS "{schema}".pedidos (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    cliente TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'aberto',
                    total NUMERIC(10,2) NOT NULL DEFAULT 0,
                    observacoes TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                '''
            )
            cur.execute(
                f'''
                CREATE TABLE IF NOT EXISTS "{schema}".itens_pedido (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    pedido_id UUID NOT NULL REFERENCES "{schema}".pedidos(id) ON DELETE CASCADE,
                    produto_id UUID NOT NULL REFERENCES "{schema}".produtos(id),
                    quantidade INTEGER NOT NULL CHECK (quantidade > 0),
                    preco_unitario NUMERIC(10,2) NOT NULL CHECK (preco_unitario >= 0),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                '''
            )
            for item in MENU:
                cur.execute(
                    f'''
                    INSERT INTO "{schema}".produtos (nome, descricao, preco, categoria, ativo)
                    VALUES (%s, %s, %s, %s, TRUE)
                    ON CONFLICT (nome) DO UPDATE
                       SET descricao = EXCLUDED.descricao,
                           preco = EXCLUDED.preco,
                           categoria = EXCLUDED.categoria,
                           ativo = TRUE,
                           updated_at = now()
                    ''',
                    (
                        item["name"],
                        item["description"],
                        item["price"],
                        item["category"],
                    ),
                )
        conn.commit()


def build_atendimento_template(service_id: str, datasource_id: str) -> dict[str, Any]:
    schema = quoted_schema()
    return {
        "_desc": "Template de atendimento da Hamburgueria Demo",
        "supervisor_prompt": (
            "Você é o atendente virtual da Hamburgueria Demo. Ajude o cliente a ver o cardápio, "
            "montar o pedido, confirmar itens e total, e só grave no banco quando houver confirmação explícita. "
            "Nunca invente produtos; sempre consulte a fonte hamburgueria. Se o cliente pedir algo inexistente, "
            "explique e ofereça alternativas válidas."
        ),
        "supervisor_ai_service_id": service_id,
        "max_steps": 8,
        "datasource_ids": [datasource_id],
        "write_tables": [
            f"{schema}.pedidos",
            f"{schema}.itens_pedido",
            "pedidos",
            "itens_pedido",
        ],
        "require_write_confirmation": True,
        "notes": "Cenário de negócio hamburgueria — atendimento",
        "agents": [
            {
                "name": "atendente_cardapio",
                "description": "consulta cardápio e pedidos da hamburgueria",
                "prompt": (
                    f"Você consulta o datasource hamburgueria. Use run_sql_query para listar itens ativos em \"{schema}\".produtos, "
                    "buscar preços e revisar pedidos existentes. Sempre retorne informações claras em português."
                ),
                "ai_service_id": service_id,
                "tools": ["run_sql_query", "calculate"],
            },
            {
                "name": "operador_pedidos",
                "description": "grava pedidos e itens confirmados",
                "prompt": (
                    f"Você grava pedidos no datasource hamburgueria usando execute_sql_transaction. "
                    f"Escreva somente nas tabelas \"{schema}\".pedidos e \"{schema}\".itens_pedido após confirmação explícita. "
                    "Ao criar um pedido, inclua cliente, status='aberto', total e observacoes quando existirem."
                ),
                "ai_service_id": service_id,
                "tools": ["run_sql_query", "execute_sql_transaction", "execute_sql_write", "calculate"],
            },
        ],
    }


def build_admin_template(service_id: str, datasource_id: str) -> dict[str, Any]:
    schema = quoted_schema()
    return {
        "_desc": "Template administrativo da Hamburgueria Demo",
        "supervisor_prompt": (
            "Você é o assistente admin da Hamburgueria Demo. Ajude o lojista a cadastrar e atualizar produtos, "
            "consultar pedidos e gerar relatórios simples de vendas. Não faça escrita sem confirmar o que será alterado."
        ),
        "supervisor_ai_service_id": service_id,
        "max_steps": 8,
        "datasource_ids": [datasource_id],
        "write_tables": [
            f"{schema}.produtos",
            "produtos",
        ],
        "require_write_confirmation": True,
        "notes": "Cenário de negócio hamburgueria — admin",
        "agents": [
            {
                "name": "gestor_cardapio",
                "description": "mantém o cardápio da hamburgueria",
                "prompt": (
                    f"Você administra o cardápio no schema \"{schema}\". Use run_sql_query para consultar e "
                    "execute_sql_write para inserir ou atualizar produtos após confirmação explícita. "
                    "Valide preço não negativo e categorias coerentes."
                ),
                "ai_service_id": service_id,
                "tools": ["run_sql_query", "execute_sql_write", "calculate"],
            },
            {
                "name": "analista_vendas",
                "description": "gera relatórios básicos de vendas e pedidos",
                "prompt": (
                    f"Você consulta pedidos e itens da hamburgueria no schema \"{schema}\". Gere relatórios simples, "
                    "como total vendido no dia, quantidade de pedidos e produtos mais vendidos, usando SQL e artifacts quando útil."
                ),
                "ai_service_id": service_id,
                "tools": ["run_sql_query", "generate_chart", "export_xlsx", "generate_pdf", "calculate"],
            },
        ],
    }


def main() -> None:
    import httpx

    prepare_database()
    with httpx.Client(base_url=BACKEND, timeout=60.0) as client:
        master_token = client.post(
            "/api/auth/login",
            json={"email": MASTER[0], "password": MASTER[1]},
        ).json()["token"]
        tenant_id = ensure_tenant(client, master_token, TENANT_NAME, TENANT_KEY, TENANT_ADMIN_EMAIL)
        token = admin_token(client, TENANT_ADMIN_EMAIL)
        services = ensure_provider_services(client, token)
        if not services:
            raise RuntimeError("Nenhum AI service configurado para a hamburgueria")
        service_id = services.get(DEFAULT_PROVIDER) or next(iter(services.values()))
        datasource_id = ensure_datasource(
            client,
            token,
            name=DATASOURCE_NAME,
            kind="postgresql",
            config={
                "host": DATASOURCE_HOST,
                "port": DATASOURCE_PORT,
                "database": POSTGRES_DB.strip(),
                "user": POSTGRES_USER.strip(),
                "schema": SCHEMA.strip(),
            },
            secret=POSTGRES_PASSWORD,
        )
        atendimento_id = ensure_template(
            client,
            token,
            "Atendimento Hamburgueria",
            build_atendimento_template(service_id, datasource_id),
        )
        admin_id = ensure_template(
            client,
            token,
            "Admin Hamburgueria",
            build_admin_template(service_id, datasource_id),
        )
        print(
            json.dumps(
                {
                    "status": "ok",
                    "tenant_id": tenant_id,
                    "datasource_id": datasource_id,
                    "templates": {
                        "atendimento": atendimento_id,
                        "admin": admin_id,
                    },
                    "tenant_admin_email": TENANT_ADMIN_EMAIL,
                    "tenant_admin_password": ADMIN_PW,
                    "schema": SCHEMA,
                    "menu_size": len(MENU),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )


if __name__ == "__main__":
    main()
