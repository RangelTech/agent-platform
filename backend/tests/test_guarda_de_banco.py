"""A suíte precisa recusar um banco que não seja descartável.

Ela apaga dados — `TRUNCATE tenants, users, user_profiles, sessions CASCADE`
entre testes — e nada perguntava para onde o `DATABASE_URL` apontava.

O caminho do acidente é curto e já foi percorrido meio a meio nesta trilha:
para conferir a migração 0023 em produção é preciso exportar o DSN real no
shell; rodar a suíte no mesmo shell alguns minutos depois apagaria todos os
tenants. Estes testes existem para que essa distância deixe de ser zero.
"""

import pytest
from guardas import exigir_banco_descartavel


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://agent:agent@localhost:5433/agent_llm",
        "postgresql://agent:agent@127.0.0.1:5432/agent_llm",
        "postgresql://agent:agent@postgres:5432/agent_llm",  # serviço do CI
    ],
)
def test_banco_local_passa(dsn):
    exigir_banco_descartavel(dsn)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://app:senha@66.94.101.153:5433/prod",
        "postgresql://app:senha@db.rangeltech.net:5432/agent_llm",
    ],
)
def test_banco_remoto_e_recusado(dsn):
    with pytest.raises(RuntimeError) as erro:
        exigir_banco_descartavel(dsn)
    assert "não é um banco local de teste" in str(erro.value)


def test_a_mensagem_nao_repete_a_senha(monkeypatch):
    """Quem lê o erro está com o DSN de produção no shell; ecoar a senha aí a
    espalharia para o log do terminal e para o histórico."""
    with pytest.raises(RuntimeError) as erro:
        exigir_banco_descartavel("postgresql://app:senha-secreta@10.0.0.9:5432/prod")
    assert "senha-secreta" not in str(erro.value)
    assert "10.0.0.9" in str(erro.value)


def test_escape_explicito_libera(monkeypatch):
    """Existe banco de teste remoto. O que não pode existir é passar sem que
    alguém tenha dito isso em voz alta."""
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TESTS", "1")
    exigir_banco_descartavel("postgresql://app:senha@10.0.0.9:5432/qualquer")
