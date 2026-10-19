from app.tool_catalog import credential_kinds


def test_terminal_do_ambiente_recebe_segredos_do_tenant():
    assert "secrets" in credential_kinds(["rt_env_manage"])


def test_sem_ferramenta_de_ambiente_nao_ha_concessao_de_segredos():
    assert "secrets" not in credential_kinds(["rt_executor_run"])
