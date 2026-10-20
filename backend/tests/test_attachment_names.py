from app.attachments import MAX_ATTACHMENT_NAME, safe_filename

BARRA = chr(92)


def test_traversal_vira_so_o_nome_do_arquivo():
    assert safe_filename("../../../tmp/pwn.txt") == "pwn.txt"
    assert safe_filename(BARRA.join(["..", "..", "windows", "x.dll"])) == "x.dll"
    assert safe_filename("/etc/passwd") == "passwd"


def test_nome_vazio_ou_so_pontos_usa_padrao():
    assert safe_filename(None) == "anexo"
    assert safe_filename("") == "anexo"
    assert safe_filename("..") == "anexo"
    assert safe_filename("   ") == "anexo"


def test_nome_longo_e_cortado_preservando_extensao():
    nome = safe_filename("x" * 400 + ".pdf")
    assert len(nome) <= MAX_ATTACHMENT_NAME and nome.endswith(".pdf")
    assert len(safe_filename("y" * 400)) == MAX_ATTACHMENT_NAME


def test_controle_bidi_e_marcacao_sao_removidos():
    assert safe_filename("arquivo" + chr(0x202E) + "gnp.exe") == "arquivognp.exe"
    assert safe_filename("a<b>c.txt") == "abc.txt"
    assert safe_filename("a" + chr(0) + "b" + chr(10) + "c.txt") == "abc.txt"


def test_nome_normal_nao_muda():
    assert safe_filename("Relatório de vendas 2026.xlsx") == "Relatório de vendas 2026.xlsx"
