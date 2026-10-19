"""Validação do pacote Markdown (função pura, sem banco)."""

from app import skill_package as pkg
from app.tool_catalog import tool_ids

TOOLS = tool_ids()

SKILL = (
    "---\nname: cobranca\ndescription: Cobra clientes em atraso\n---\n"
    "# Cobrança\n\nPasso a passo.\n"
)


def _files(**extra):
    files = {"AGENTE.md": "# Agente\n\nSeja objetivo.\n", "skills/cobranca/SKILL.md": SKILL}
    files.update(extra)
    return [{"path": p, "content": c} for p, c in files.items() if c is not None]


def codes(problems):
    return {p.code for p in problems}


def test_valid_package_has_no_errors():
    problems = pkg.validate_package(_files(), {"tools": ["rt_sql_read"]}, TOOLS)
    assert not pkg.has_errors(problems), [p.as_dict() for p in problems]


def test_initial_package_is_valid():
    files, manifest = pkg.initial_package()
    assert not pkg.has_errors(pkg.validate_package(files, manifest, TOOLS))


def test_missing_agent_md():
    files = [f for f in _files() if f["path"] != "AGENTE.md"]
    assert "missing_agent_md" in codes(pkg.validate_package(files, {}, TOOLS))


def test_bad_frontmatter_and_bad_name():
    bad = _files(**{"skills/cobranca/SKILL.md": "sem cabecalho"})
    assert "bad_frontmatter" in codes(pkg.validate_package(bad, {}, TOOLS))
    wrong = _files(
        **{"skills/cobranca/SKILL.md": "---\nname: Cobranca Ruim\ndescription: x\n---\ncorpo"}
    )
    assert "bad_name" in codes(pkg.validate_package(wrong, {}, TOOLS))
    mismatch = _files(
        **{"skills/cobranca/SKILL.md": "---\nname: outra\ndescription: x\n---\ncorpo"}
    )
    assert "bad_name" in codes(pkg.validate_package(mismatch, {}, TOOLS))
    reserved = _files(**{"skills/u-x/SKILL.md": "---\nname: u-x\ndescription: x\n---\ncorpo"})
    assert "bad_name" in codes(pkg.validate_package(reserved, {}, TOOLS))


def test_unclosed_and_invalid_yaml_frontmatter():
    unclosed = _files(**{"skills/cobranca/SKILL.md": "---\nname: cobranca\ncorpo"})
    assert "bad_frontmatter" in codes(pkg.validate_package(unclosed, {}, TOOLS))
    invalid = _files(**{"skills/cobranca/SKILL.md": "---\nname: [a\n---\ncorpo"})
    assert "bad_frontmatter" in codes(pkg.validate_package(invalid, {}, TOOLS))


def test_duplicate_skill_name_across_folders():
    # Mesmo nome (case-insensitive) em duas pastas; a pasta diferente já é bad_name,
    # mas o duplicado precisa ser apontado.
    files = _files(
        **{
            "skills/cobranca/SKILL.md": SKILL,
            "skills/Cobranca/SKILL.md": "---\nname: Cobranca\ndescription: x\n---\nz",
        }
    )
    assert "duplicate_name" in codes(pkg.validate_package(files, {}, TOOLS))


def test_paths():
    for bad in [
        "../x.md",
        "/etc/passwd",
        "skills/../AGENTE.md",
        ".oculto/a.md",
        "a\\b.md",
        "C:/x.md",
    ]:
        problems = pkg.validate_package(_files(**{bad: "x"}), {}, TOOLS)
        assert "bad_path" in codes(problems), bad
    assert "bad_path" in codes(pkg.validate_package(_files(**{"solto.md": "x"}), {}, TOOLS))


def test_extensions():
    ok = _files(
        **{
            "skills/cobranca/references/a.md": "texto",
            "skills/cobranca/scripts/run.py": "print(1)",
            "skills/cobranca/templates/mail.html": "<p>oi</p>",
        }
    )
    assert not pkg.has_errors(pkg.validate_package(ok, {}, TOOLS))
    for path in [
        "skills/cobranca/references/a.txt",
        "skills/cobranca/scripts/run.rb",
        "skills/cobranca/templates/a.exe",
    ]:
        problems = pkg.validate_package(_files(**{path: "x"}), {}, TOOLS)
        assert "bad_extension" in codes(problems), path


def test_size_limits():
    big = "a" * (200 * 1024 + 1)
    assert "file_too_large" in codes(
        pkg.validate_package(_files(**{"skills/cobranca/references/g.md": big}), {}, TOOLS)
    )
    many = _files(**{f"skills/cobranca/references/r{i}.md": "x" for i in range(100)})
    assert "too_many_files" in codes(pkg.validate_package(many, {}, TOOLS))
    heavy = _files(**{f"skills/cobranca/references/h{i}.md": "a" * (190 * 1024) for i in range(12)})
    assert "package_too_large" in codes(pkg.validate_package(heavy, {}, TOOLS))


def test_bad_encoding():
    problems = pkg.validate_package(
        _files(**{"skills/cobranca/references/n.md": "a\x00b"}), {}, TOOLS
    )
    assert "bad_encoding" in codes(problems)
    problems = pkg.validate_package(
        _files(**{"skills/cobranca/references/s.md": "a\ud800b"}), {}, TOOLS
    )
    assert "bad_encoding" in codes(problems)


def test_secret_detection_does_not_echo_the_value():
    secret = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    problems = pkg.validate_package(
        _files(**{"skills/cobranca/references/k.md": f"use a chave {secret} aqui"}), {}, TOOLS
    )
    found = [p for p in problems if p.code == "secret_detected"]
    assert found and found[0].line == 1
    assert secret not in found[0].message
    for text in [
        "-----BEGIN RSA PRIVATE KEY-----",
        "AKIAABCDEFGHIJKLMNOP",
        "senha: Zx9Qw3Er7Ty1U",
        "api_key = 'abc123def456ghi789'",
    ]:
        assert pkg.find_secrets(text), text
    # Referências a credenciais da empresa não são segredos.
    for text in [
        "token: {{secret:MEU_TOKEN}}",
        "senha: <sua senha>",
        "use o token do Mercado Pago",
    ]:
        assert not pkg.find_secrets(text), text


def test_broken_reference():
    files = _files(
        **{
            "skills/cobranca/SKILL.md": SKILL
            + "\nVeja [guia](references/guia.md) e [site](https://x.com)\n"
        }
    )
    problems = pkg.validate_package(files, {}, TOOLS)
    broken = [p for p in problems if p.code == "broken_reference"]
    assert broken and broken[0].path == "skills/cobranca/SKILL.md"
    files.append({"path": "skills/cobranca/references/guia.md", "content": "ok"})
    assert "broken_reference" not in codes(pkg.validate_package(files, {}, TOOLS))


def test_manifest_validation():
    assert "unknown_tool" in codes(
        pkg.validate_package(_files(), {"tools": ["rt_inexistente"]}, TOOLS)
    )
    assert "bad_manifest" in codes(pkg.validate_package(_files(), {"tools": "x"}, TOOLS))
    assert "bad_manifest" in codes(
        pkg.validate_package(_files(), {"limits": {"max_turns": 999}}, TOOLS)
    )
    normalized = pkg.normalize_manifest({"tools": ["rt_sql_read", 3], "lixo": 1})
    assert normalized["tools"] == ["rt_sql_read"] and "lixo" not in normalized


def test_user_skill_rules():
    ok = "---\nname: u-resumo\ndescription: Resume reunioes\n---\nFaça resumos.\n"
    problems, data = pkg.validate_user_skill(ok, "u-resumo")
    assert not pkg.has_errors(problems) and data["name"] == "u-resumo"
    bad_prefix = ok.replace("u-resumo", "resumo")
    assert "bad_name" in codes(pkg.validate_user_skill(bad_prefix)[0])
    assert "bad_name" in codes(pkg.validate_user_skill(ok, "u-outro")[0])
    huge = ok + "x" * (33 * 1024)
    assert "file_too_large" in codes(pkg.validate_user_skill(huge)[0])
    leaked = ok + "\nsenha: Zx9Qw3Er7Ty1U\n"
    assert "secret_detected" in codes(pkg.validate_user_skill(leaked)[0])


def test_frontmatter_roundtrip():
    data, body, err = pkg.split_frontmatter(SKILL)
    assert err is None and data["name"] == "cobranca"
    data["description"] = "Nova"
    again, _body, err = pkg.split_frontmatter(pkg.render_frontmatter(data, body))
    assert err is None and again["description"] == "Nova"


def test_manifest_limits_write_tables_and_hosts_are_kept_and_cleaned():
    raw = {
        "tools": ["rt_sql_write", "rt_http"],
        "limits": {
            "max_turns": 30,
            "write_tables": ["vendas", "public.pedidos", "vendas", "1ruim", "x; drop"],
            "http_allowed_hosts": ["API.Exemplo.com", "api.exemplo.com", "ip-literal", ""],
        },
    }
    got = pkg.normalize_manifest(raw)["limits"]
    assert got["max_turns"] == 30
    assert got["write_tables"] == ["vendas", "public.pedidos"]
    assert got["http_allowed_hosts"] == ["api.exemplo.com"]


def test_manifest_limits_validation_flags_bad_entries():
    ok = {"limits": {"write_tables": ["vendas"], "http_allowed_hosts": ["api.exemplo.com"]}}
    assert not pkg.has_errors(pkg.validate_package(_files(), ok, TOOLS))
    bad = {"limits": {"write_tables": ["vendas; drop table x"], "http_allowed_hosts": "nao-lista"}}
    problems = pkg.validate_package(_files(), bad, TOOLS)
    assert "bad_manifest" in codes(problems)
    assert pkg.has_errors(problems)
    assert pkg.has_errors(pkg.validate_package(_files(), {"limits": 7}, TOOLS))


def test_manifest_confirm_actions_is_kept_only_when_true_and_validated():
    on = pkg.normalize_manifest({"limits": {"confirm_actions": True}})["limits"]
    assert on == {"confirm_actions": True}
    off = pkg.normalize_manifest({"limits": {"confirm_actions": False}})["limits"]
    assert "confirm_actions" not in off
    bad = pkg.validate_package(_files(), {"limits": {"confirm_actions": "sim"}}, TOOLS)
    assert pkg.has_errors(bad)


# ----------------------------------------------------- arquivos/ (H-2) ---


def test_assets_dir_accepts_any_data_extension():
    for name in ("cardapio.pdf", "precos.xlsx", "foto.JPG", "dados.csv", "x.zip", "sem-extensao"):
        assert pkg.classify_path(f"arquivos/{name}") == ("asset", None), name
    assert pkg.classify_path("arquivos/sub/pasta/tabela.parquet") == ("asset", None)


def test_assets_dir_denies_executables_and_scripts():
    for name in ("a.exe", "a.dll", "a.BAT", "a.sh", "a.py", "a.js", "a.ps1", "a.jar", "a.so"):
        kind, err = pkg.classify_path(f"arquivos/{name}")
        assert kind is None and "scripts/" in err, name


def test_assets_path_rules_still_apply():
    bads = ("arquivos/../x.pdf", "arquivos/.oculto.pdf", "/arquivos/x.pdf", "arquivos/a\nb.pdf")
    for bad in bads:
        assert pkg.classify_path(bad)[0] is None, bad


def test_validate_asset_checks_size_magic_and_secrets():
    ok = pkg.validate_asset("arquivos/c.pdf", b"%PDF-1.7\n\xe2\xe3\xcf\xd3\n")
    assert not pkg.has_errors(ok)  # binário que não é UTF-8 é permitido
    assert "empty_file" in codes(pkg.validate_asset("arquivos/c.pdf", b""))
    big = b"0" * (pkg.MAX_ASSET_BYTES + 1)
    assert "file_too_large" in codes(pkg.validate_asset("arquivos/c.pdf", big))
    for blob in (b"MZ\x90\x00", b"\x7fELF\x02", b"#!/bin/sh\nrm -rf /"):
        assert "bad_extension" in codes(pkg.validate_asset("arquivos/inocente.pdf", blob)), blob
    assert "bad_extension" in codes(pkg.validate_asset("arquivos/x.exe", b"abc"))
    leaked = b"chave,valor\nsenha=Abc123def456ghi\n"
    assert "secret_detected" in codes(pkg.validate_asset("arquivos/tabela.csv", leaked))
    # Fora de arquivos/ não é asset.
    assert pkg.has_errors(pkg.validate_asset("skills/x/SKILL.md", b"abc"))


def test_package_counts_assets_apart_from_text_limits():
    files = _files() + [{"path": "arquivos/c.pdf", "size_bytes": 9 * 1024 * 1024}]
    assert not pkg.has_errors(pkg.validate_package(files, {}, TOOLS))  # > 2 MiB de texto, ok
    too_big = _files() + [{"path": "arquivos/c.pdf", "size_bytes": pkg.MAX_ASSET_BYTES + 1}]
    assert "file_too_large" in codes(pkg.validate_package(too_big, {}, TOOLS))
    many = _files() + [
        {"path": f"arquivos/f{i}.bin2", "size_bytes": 9 * 1024 * 1024} for i in range(6)
    ]
    assert "package_too_large" in codes(pkg.validate_package(many, {}, TOOLS))
    dup = _files() + [{"path": "arquivos/a.pdf", "size_bytes": 1}] * 2
    assert pkg.has_errors(pkg.validate_package(dup, {}, TOOLS))


def test_reference_to_asset_is_checked_against_the_folder():
    md = "# Agente\n\nVeja o [cardápio](arquivos/cardapio.pdf).\n"
    files = [f for f in _files() if f["path"] != "AGENTE.md"]
    files.append({"path": "AGENTE.md", "content": md})
    assert "broken_reference" in codes(pkg.validate_package(files, {}, TOOLS))
    files.append({"path": "arquivos/cardapio.pdf", "size_bytes": 10})
    assert not pkg.has_errors(pkg.validate_package(files, {}, TOOLS))
