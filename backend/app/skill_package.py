"""Pacote Markdown de um agente por skills: validação, rascunho inicial e
frontmatter.

Tudo aqui é função pura (sem banco), para ser testada isoladamente e reusada
pelo editor do template (`routes/templates.py`) e pelas skills do usuário
(`routes/skills.py`). A validação nunca bloqueia o *rascunho*; só bloqueia a
criação de uma versão.

Mensagens são para o cliente final: nada de nomes internos de componentes.
"""

from __future__ import annotations

import hashlib
import mimetypes
import posixpath
import re
from dataclasses import dataclass

import yaml

MAX_FILE_BYTES = 200 * 1024
MAX_SKILL_MD_CHARS = 100_000
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_FILES = 100
MAX_USER_SKILL_BYTES = 32 * 1024
MAX_USER_SKILLS_ACTIVE = 50
MAX_DESCRIPTION = 1024

# Pasta reservada para arquivos de qualquer tipo (PDF de cardápio, planilha, imagem...).
# Fica fora das regras de skills: é dado para o agente ler, nunca instrução nem código.
ASSETS_DIR = "arquivos"
MAX_ASSET_BYTES = 10 * 1024 * 1024
MAX_ASSETS_TOTAL_BYTES = 50 * 1024 * 1024
MAX_ASSETS = 100
# Executáveis e scripts não entram em arquivos/: código de verdade vai em
# skills/<nome>/scripts/, que tem revisão (extensão, segredos, tamanho). Aqui o
# agente só LÊ; barrar o que o sistema operacional executaria evita que a pasta
# vire um jeito de contrabandear programa sem passar pela revisão.
DENIED_ASSET_EXTENSIONS = frozenset(
    {
        ".exe", ".dll", ".sys", ".msi", ".com", ".scr", ".bat", ".cmd", ".ps1", ".psm1",
        ".vbs", ".vbe", ".wsf", ".jar", ".apk", ".app", ".dmg", ".pkg", ".deb", ".rpm",
        ".so", ".dylib", ".bin", ".elf", ".sh", ".bash", ".zsh", ".py", ".pyc", ".js",
        ".mjs", ".php", ".pl", ".rb", ".lnk", ".reg", ".hta",
    }
)  # fmt: skip
_EXECUTABLE_MAGIC = (
    (b"MZ", "programa do Windows"),
    (b"\x7fELF", "programa do Linux"),
    (b"\xca\xfe\xba\xbe", "programa executável"),
    (b"\xcf\xfa\xed\xfe", "programa do macOS"),
    (b"\xce\xfa\xed\xfe", "programa do macOS"),
    (b"#!", "script executável"),
)
MAX_ASSET_SECRET_SCAN_BYTES = 1024 * 1024

SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
USER_SKILL_NAME = re.compile(r"^u-[a-z0-9][a-z0-9._-]*$")
MAX_NAME = 64

SCRIPT_EXTENSIONS = (".py", ".sh", ".js")
TEMPLATE_EXTENSIONS = (".md", ".txt", ".html", ".htm", ".json", ".csv", ".yaml", ".yml", ".j2")

_ASSET_TEXT_EXTENSIONS = frozenset(TEMPLATE_EXTENSIONS) | {".xml", ".tsv", ".log"}

_SKILL_PATH = re.compile(r"^skills/([^/]+)/SKILL\.md$")
_SKILL_SUBPATH = re.compile(r"^skills/([^/]+)/(references|scripts|templates)/(.+)$")

# (rótulo, regex). O rótulo vai ao cliente; o trecho encontrado nunca.
_SECRET_PATTERNS = [
    ("chave privada", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("chave de acesso da AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("chave de API", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("chave do Google", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("token do GitHub", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("token do Slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    (
        "token JWT",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ),
    ("token do Mercado Pago", re.compile(r"\b(?:APP_USR|TEST)-[0-9a-f-]{20,}")),
    (
        "senha, token ou chave escrita no texto",
        re.compile(
            r"""(?ix)
            \b(?:password|passwd|senha|secret|segredo|token|api[_-]?key|access[_-]?key)\b
            \s*[:=]\s*
            ["']?(?!\{\{|\$|<|your|seu|sua|exemplo|example|xxx|\*\*\*|changeme)
            (?=[^\s"']*\d)
            [A-Za-z0-9/+_\-.=!@#%^&*]{12,}"""
        ),
    ),
]

_LINK = re.compile(r"\]\(([^)\s]+)\)")


@dataclass
class Problem:
    path: str
    code: str
    message: str
    severity: str = "error"
    line: int | None = None

    def as_dict(self) -> dict:
        out = {
            "path": self.path,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }
        if self.line is not None:
            out["line"] = self.line
        return out


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_asset_path(path: object) -> bool:
    return isinstance(path, str) and path.startswith(ASSETS_DIR + "/")


def guess_mime(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def size_bytes(text: str) -> int:
    return len(text.encode("utf-8", errors="replace"))


# ---------------------------------------------------------------- frontmatter


def split_frontmatter(content: str) -> tuple[dict | None, str, str | None]:
    """(dados, corpo, erro). `dados` é None quando não há frontmatter."""
    text = content.lstrip("﻿")
    if not text.startswith("---"):
        return None, text, None
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        return None, text, "O cabeçalho deve começar com uma linha contendo apenas ---"
    end = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            end = i
            break
    if end is None:
        return None, text, "O cabeçalho não foi fechado com ---"
    raw = "\n".join(lines[1:end])
    try:
        data = yaml.safe_load(raw) if raw.strip() else {}
    except yaml.YAMLError:
        return None, text, "O cabeçalho não é um YAML válido"
    if not isinstance(data, dict):
        return None, text, "O cabeçalho deve ser uma lista de campos (nome: valor)"
    return data, "\n".join(lines[end + 1 :]), None


def render_frontmatter(data: dict, body: str) -> str:
    dumped = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{dumped}\n---\n{body.lstrip(chr(10))}"


# ------------------------------------------------------------------ segredos


def find_secrets(content: str) -> list[tuple[int, str]]:
    """[(linha, rótulo)]. Não devolve o trecho encontrado."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(content.split("\n"), start=1):
        for label, pattern in _SECRET_PATTERNS:
            if pattern.search(line):
                found.append((number, label))
                break
    return found


# ----------------------------------------------------------------- caminhos


def classify_path(path: str) -> tuple[str | None, str | None]:
    """(kind, erro). kind: agent|skill|reference|script|template."""
    if not isinstance(path, str) or not path:
        return None, "Caminho vazio"
    if "\\" in path or "\x00" in path:
        return None, "Caminho com caracteres inválidos"
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return None, "Caminho absoluto não é permitido"
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None, "Caminho com segmentos inválidos (.., vazio ou .)"
    if any(p.startswith(".") for p in parts):
        return None, "Arquivos e pastas ocultos não são permitidos"
    if len(path) > 240:
        return None, "Caminho longo demais"
    if path == "AGENTE.md":
        return "agent", None
    if path.startswith(ASSETS_DIR + "/"):
        return _classify_asset(path)
    if _SKILL_PATH.match(path):
        return "skill", None
    sub = _SKILL_SUBPATH.match(path)
    if sub:
        folder, ext = sub.group(2), posixpath.splitext(path)[1].lower()
        if folder == "references":
            if ext != ".md":
                return None, "Em references/ só são aceitos arquivos .md"
            return "reference", None
        if folder == "scripts":
            if ext not in SCRIPT_EXTENSIONS:
                return None, "Em scripts/ só são aceitos .py, .sh e .js"
            return "script", None
        if ext not in TEMPLATE_EXTENSIONS:
            return None, "Extensão não permitida em templates/"
        return "template", None
    if path.endswith("/SKILL.md") or path.startswith("skills/"):
        return None, "Caminho fora da estrutura skills/<nome>/SKILL.md"
    return None, "Caminho fora da estrutura permitida (AGENTE.md e skills/<nome>/...)"


def _classify_asset(path: str) -> tuple[str | None, str | None]:
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        return None, "Nome com caracteres inválidos"
    ext = posixpath.splitext(path)[1].lower()
    if ext in DENIED_ASSET_EXTENSIONS:
        return None, (
            f"Arquivos {ext} não são aceitos em arquivos/. "
            "Código vai em skills/<nome>/scripts/, onde é revisado"
        )
    return "asset", None


def validate_asset(path: str, data: bytes) -> list[Problem]:
    """Valida UM arquivo de arquivos/ já com os bytes (no envio pela tela)."""
    kind, err = classify_path(path)
    if err or kind != "asset":
        code = "bad_extension" if err and "não são aceitos" in err else "bad_path"
        return [Problem(path, code, err or "Caminho fora da pasta arquivos/")]
    problems: list[Problem] = []
    if not data:
        problems.append(Problem(path, "empty_file", "O arquivo está vazio"))
    if len(data) > MAX_ASSET_BYTES:
        problems.append(
            Problem(path, "file_too_large", f"O arquivo passa de {MAX_ASSET_BYTES // 2**20} MB")
        )
    for magic, label in _EXECUTABLE_MAGIC:
        if data.startswith(magic):
            problems.append(
                Problem(
                    path,
                    "bad_extension",
                    f"O conteúdo parece ser um {label}; só dados são aceitos em arquivos/",
                )
            )
            break
    ext = posixpath.splitext(path)[1].lower()
    if ext in _ASSET_TEXT_EXTENSIONS and len(data) <= MAX_ASSET_SECRET_SCAN_BYTES:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        for number, label in find_secrets(text):
            problems.append(
                Problem(
                    path,
                    "secret_detected",
                    f"Parece haver {label} escrito no texto. "
                    "Remova e use as credenciais da empresa",
                    line=number,
                )
            )
    return problems


# ------------------------------------------------------------------- pacote


def default_manifest() -> dict:
    return {
        "tools": [],
        "environment": {"enabled": False},
        "model": None,
        "limits": {},
        "output": {"format": DEFAULT_OUTPUT_FORMAT},
    }


# Formato em que o agente escreve a resposta (por canal): markdown (web), whatsapp, text.
OUTPUT_FORMATS = ("markdown", "whatsapp", "text")

# Toolsets nativos do worker que o manifesto pode ligar. Espelha o OPTIONAL/FORBIDDEN_TOOLSETS do
# kernel-harness (profiles/profile_config.py): o kernel recusa a run com toolset fora desta lista.
OPTIONAL_TOOLSETS = ("web", "vision", "image_gen")
FORBIDDEN_TOOLSETS = frozenset({
    "terminal", "file", "code_execution", "browser", "cronjob", "delegation", "tts", "kanban",
    "computer_use", "connections", "clarify",
})
DEFAULT_OUTPUT_FORMAT = "markdown"


def output_format_of(manifest: object) -> str:
    """Formato de saída do manifesto, já normalizado (desconhecido cai em markdown)."""
    out = manifest.get("output") if isinstance(manifest, dict) else None
    fmt = out.get("format") if isinstance(out, dict) else None
    return fmt if fmt in OUTPUT_FORMATS else DEFAULT_OUTPUT_FORMAT


def normalize_manifest(raw: object) -> dict:
    """Formato canônico; campos desconhecidos são descartados."""
    base = default_manifest()
    if not isinstance(raw, dict):
        return base
    tools = raw.get("tools")
    base["tools"] = [t for t in tools if isinstance(t, str)] if isinstance(tools, list) else []
    toolsets = raw.get("toolsets")
    if isinstance(toolsets, list):
        # so os liberaveis (web, vision, image_gen); proibidos e desconhecidos sao descartados aqui
        # (validate_manifest avisa quem salva). Sem toolsets nao ha chave: formato canonico igual.
        kept = [t for t in dict.fromkeys(toolsets) if t in OPTIONAL_TOOLSETS]
        if kept:
            base["toolsets"] = kept
    env = raw.get("environment")
    base["environment"] = {"enabled": bool(env.get("enabled")) if isinstance(env, dict) else False}
    model = raw.get("model")
    # `combo` is resolved by the tenant-aware gateway BFF.  Keep it in the
    # package so a published template carries the intended routing policy;
    # the engine deliberately does not resolve a combo by itself.
    if isinstance(model, dict) and _valid_model_reference(model.get("combo")):
        base["model"] = {"combo": model["combo"].strip()}
    elif isinstance(model, dict) and _valid_model_reference(model.get("alias")):
        base["model"] = {"alias": model["alias"].strip()}
    base["output"] = {"format": output_format_of(raw)}
    limits = raw.get("limits")
    if isinstance(limits, dict):
        out: dict = {}
        if isinstance(limits.get("max_turns"), int):
            out["max_turns"] = limits["max_turns"]
        if limits.get("luar") is True:
            out["luar"] = True  # o agente pode operar o Luar como o usuário do chat (H-6)
        ca = limits.get("confirm_actions")
        if ca is True:
            out["confirm_actions"] = True  # liga o cartão (padrão: o agente age direto)
        elif isinstance(ca, list):
            kinds = [k for k in ca if isinstance(k, str) and CONFIRM_KIND_RE.match(k)]
            if kinds:
                out["confirm_actions"] = kinds  # cartão só para estes tipos de ação
        if limits.get("strict_scope") is True:
            out["strict_scope"] = True  # desliga o atalho de quem edita agentes (Sr Moon)
        scopes = _clean_list(limits.get("allowed_scopes"), SCOPE_RE_AI, lower=True)
        if scopes:
            out["allowed_scopes"] = scopes  # quem pode usar este agente (any-of)
        tables = _clean_list(limits.get("write_tables"), TABLE_RE)
        if tables:
            out["write_tables"] = tables
        hosts = _clean_list(limits.get("http_allowed_hosts"), HOST_RE, lower=True)
        if hosts:
            out["http_allowed_hosts"] = hosts
        base["limits"] = out
    return base


CONFIRM_KIND_RE = re.compile(r"^[a-z][a-z0-9_:]{0,59}$")
TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}(\.[A-Za-z_][A-Za-z0-9_]{0,62})?$")
HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
MAX_LIST = 50
SCOPE_RE_AI = re.compile(r"^ai\.[a-z_]+\.[a-z_]+$")


def _valid_model_reference(value: object) -> bool:
    """A bounded, non-blank routing reference (alias or tenant combo name)."""
    return isinstance(value, str) and 0 < len(value.strip()) <= 120


def _clean_list(value: object, pattern: re.Pattern, lower: bool = False) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        item = item.strip().lower() if lower else item.strip()
        if pattern.match(item) and item not in out:
            out.append(item)
    return out[:MAX_LIST]


def validate_manifest(raw: object, tool_ids: set[str]) -> list[Problem]:
    problems: list[Problem] = []
    if raw is None:
        return problems
    if not isinstance(raw, dict):
        return [Problem("manifest", "bad_manifest", "O manifesto deve ser um objeto")]
    tools = raw.get("tools", [])
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        problems.append(Problem("manifest", "bad_manifest", "A lista de ferramentas é inválida"))
    else:
        for tool in dict.fromkeys(tools):
            if tool not in tool_ids:
                problems.append(
                    Problem("manifest", "unknown_tool", f"Ferramenta desconhecida: {tool}")
                )
    toolsets = raw.get("toolsets")
    if toolsets is not None:
        if not isinstance(toolsets, list) or not all(isinstance(t, str) for t in toolsets):
            problems.append(Problem("manifest", "bad_manifest", "A lista de toolsets é inválida"))
        else:
            for name in dict.fromkeys(toolsets):
                if name in FORBIDDEN_TOOLSETS:
                    problems.append(
                        Problem("manifest", "forbidden_toolset", f"Toolset não permitido: {name}")
                    )
                elif name not in OPTIONAL_TOOLSETS:
                    problems.append(
                        Problem("manifest", "unknown_toolset", f"Toolset desconhecido: {name}")
                    )
    env = raw.get("environment")
    if env is not None and not (
        isinstance(env, dict) and isinstance(env.get("enabled", False), bool)
    ):
        problems.append(Problem("manifest", "bad_manifest", "Configuração de ambiente inválida"))
    model = raw.get("model")
    model_is_valid = (
        isinstance(model, dict)
        and set(model).issubset({"alias", "combo"})
        and sum(_valid_model_reference(model.get(key)) for key in ("alias", "combo")) == 1
    )
    if model is not None and not model_is_valid:
        problems.append(Problem("manifest", "bad_manifest", "Configuração de modelo inválida"))
    output = raw.get("output")
    if output is not None:
        fmt = output.get("format") if isinstance(output, dict) else None
        if not isinstance(output, dict) or (fmt is not None and fmt not in OUTPUT_FORMATS):
            problems.append(
                Problem(
                    "manifest",
                    "bad_manifest",
                    "O formato de saída deve ser markdown, whatsapp ou text",
                )
            )
    limits = raw.get("limits")
    if limits is not None and not isinstance(limits, dict):
        problems.append(Problem("manifest", "bad_manifest", "Os limites são inválidos"))
    elif isinstance(limits, dict):
        for key, pattern, label, lower in (
            ("write_tables", TABLE_RE, "tabela", False),
            ("http_allowed_hosts", HOST_RE, "endereço", True),
        ):
            vals = limits.get(key)
            if vals is None:
                continue
            if not isinstance(vals, list) or len(vals) > MAX_LIST:
                problems.append(Problem("manifest", "bad_manifest", f"Lista de {label}s inválida"))
                continue
            for v in vals:
                text = v.strip().lower() if (lower and isinstance(v, str)) else v
                if not (isinstance(text, str) and pattern.match(text.strip())):
                    problems.append(
                        Problem("manifest", "bad_manifest", f"Valor inválido em {label}s: {v!r}")
                    )
    if isinstance(limits, dict):
        ca = limits.get("confirm_actions")
        if "confirm_actions" in limits and not (
            isinstance(ca, bool)
            or (
                isinstance(ca, list)
                and all(isinstance(k, str) and CONFIRM_KIND_RE.match(k) for k in ca)
            )
        ):
            problems.append(
                Problem("manifest", "bad_manifest",
                        "confirm_actions deve ser verdadeiro/falso ou uma lista de tipos de ação")
            )
        turns = limits.get("max_turns")
        if turns is not None and not (
            isinstance(turns, int) and not isinstance(turns, bool) and 1 <= turns <= 200
        ):
            problems.append(
                Problem("manifest", "bad_manifest", "O limite de turnos deve ficar entre 1 e 200")
            )
    return problems


def validate_package(files: object, manifest: object, tool_ids: set[str]) -> list[Problem]:
    """Valida o pacote inteiro. `files`: [{path, content}]."""
    problems: list[Problem] = []
    if not isinstance(files, list):
        return [Problem("", "bad_path", "A lista de arquivos é inválida")]

    entries: dict[str, str] = {}
    assets: dict[str, int] = {}
    total = 0
    for item in files:
        path = item.get("path") if isinstance(item, dict) else None
        content = item.get("content") if isinstance(item, dict) else None
        if is_asset_path(path):
            # Arquivo de arquivos/: só o descritor {path, size_bytes} entra aqui; os bytes
            # já foram validados no envio (validate_asset).
            _kind, err = classify_path(path)
            raw_size = item.get("size_bytes")
            if isinstance(content, str):
                raw_size = size_bytes(content)
            if err:
                code = "bad_extension" if "não são aceitos" in err else "bad_path"
                problems.append(Problem(path, code, err))
            elif path in assets:
                problems.append(Problem(path, "bad_path", "Arquivo repetido no pacote"))
            elif not isinstance(raw_size, int) or isinstance(raw_size, bool) or raw_size < 0:
                problems.append(Problem(path, "bad_encoding", "Conteúdo inválido"))
            else:
                assets[path] = raw_size
                if raw_size > MAX_ASSET_BYTES:
                    problems.append(
                        Problem(
                            path,
                            "file_too_large",
                            f"O arquivo passa de {MAX_ASSET_BYTES // 2**20} MB",
                        )
                    )
            continue
        if not isinstance(content, str):
            problems.append(Problem(str(path or ""), "bad_encoding", "Conteúdo inválido"))
            continue
        kind, err = classify_path(path) if isinstance(path, str) else (None, "Caminho vazio")
        if err:
            code = "bad_extension" if "Em " in err or "Extensão" in err else "bad_path"
            problems.append(Problem(str(path or ""), code, err))
            continue
        if path in entries:
            problems.append(Problem(path, "bad_path", "Arquivo repetido no pacote"))
            continue
        try:
            content.encode("utf-8")
        except UnicodeEncodeError:
            problems.append(Problem(path, "bad_encoding", "O arquivo deve estar em UTF-8"))
            continue
        if "\x00" in content:
            problems.append(Problem(path, "bad_encoding", "Arquivo binário ou com byte nulo"))
            continue
        entries[path] = content
        size = size_bytes(content)
        total += size
        if size > MAX_FILE_BYTES:
            problems.append(Problem(path, "file_too_large", "O arquivo passa de 200 KiB"))
        elif kind == "skill" and len(content) > MAX_SKILL_MD_CHARS:
            problems.append(Problem(path, "file_too_large", "A skill passa de 100.000 caracteres"))

    if len(files) - len(assets) > MAX_FILES:
        problems.append(Problem("", "too_many_files", f"O pacote tem mais de {MAX_FILES} arquivos"))
    if len(assets) > MAX_ASSETS:
        problems.append(
            Problem("", "too_many_files", f"A pasta arquivos/ tem mais de {MAX_ASSETS} arquivos")
        )
    if sum(assets.values()) > MAX_ASSETS_TOTAL_BYTES:
        problems.append(
            Problem(
                "", "package_too_large",
                f"Os arquivos passam de {MAX_ASSETS_TOTAL_BYTES // 2**20} MB no total",
            )
        )
    if total > MAX_PACKAGE_BYTES:
        problems.append(Problem("", "package_too_large", "O pacote passa de 2 MiB"))

    # AGENTE.md
    agent = entries.get("AGENTE.md")
    if agent is None or not agent.strip():
        problems.append(
            Problem("AGENTE.md", "missing_agent_md", "O arquivo AGENTE.md é obrigatório")
        )
    elif agent.lstrip("﻿").startswith("---"):
        _data, _body, err = split_frontmatter(agent)
        if err:
            problems.append(Problem("AGENTE.md", "bad_frontmatter", err, line=1))

    # Skills: frontmatter, nome, duplicidade
    names: dict[str, str] = {}
    for path, content in sorted(entries.items()):
        match = _SKILL_PATH.match(path)
        if not match:
            continue
        folder = match.group(1)
        data, _body, err = split_frontmatter(content)
        if err or data is None:
            problems.append(
                Problem(
                    path,
                    "bad_frontmatter",
                    err or "A skill precisa de um cabeçalho com nome e descrição",
                    line=1,
                )
            )
            continue
        name = data.get("name")
        description = data.get("description")
        if not isinstance(name, str) or not name:
            problems.append(Problem(path, "bad_frontmatter", "Falta o campo name", line=1))
        else:
            if len(name) > MAX_NAME or not SKILL_NAME.match(name):
                problems.append(
                    Problem(
                        path,
                        "bad_name",
                        "Nome inválido: use letras minúsculas, números, ponto, hífen ou "
                        "sublinhado (até 64 caracteres)",
                        line=1,
                    )
                )
            elif name != folder:
                problems.append(
                    Problem(path, "bad_name", "O nome deve ser igual ao nome da pasta", line=1)
                )
            elif name.startswith("u-"):
                problems.append(
                    Problem(path, "bad_name", "O prefixo u- é reservado às skills pessoais", line=1)
                )
            key = name.lower()
            if key in names and names[key] != path:
                problems.append(
                    Problem(path, "duplicate_name", f"Já existe outra skill chamada {name}")
                )
            names.setdefault(key, path)
        if not isinstance(description, str) or not description.strip():
            problems.append(Problem(path, "bad_frontmatter", "Falta o campo description", line=1))
        elif len(description) > MAX_DESCRIPTION:
            problems.append(
                Problem(path, "bad_frontmatter", "A descrição passa de 1024 caracteres", line=1)
            )

    # Pastas de skill sem SKILL.md
    folders = {
        m.group(1) for p in entries if (m := _SKILL_SUBPATH.match(p) or _SKILL_PATH.match(p))
    }
    for folder in sorted(folders):
        if f"skills/{folder}/SKILL.md" not in entries:
            problems.append(
                Problem(
                    f"skills/{folder}",
                    "bad_path",
                    "A pasta da skill não tem o arquivo SKILL.md",
                )
            )

    # Referências quebradas
    for path, content in sorted(entries.items()):
        if not path.endswith(".md"):
            continue
        base = posixpath.dirname(path)
        for number, line in enumerate(content.split("\n"), start=1):
            for target in _LINK.findall(line):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith(("#", "/")):
                    continue
                target = target.split("#", 1)[0]
                if not target:
                    continue
                resolved = posixpath.normpath(posixpath.join(base, target))
                if not re.match(
                    rf"^(skills/[^/]+/)?(references|scripts|templates)/|^{ASSETS_DIR}/", resolved
                ) and (resolved != "AGENTE.md"):
                    continue
                if resolved not in entries and resolved not in assets:
                    problems.append(
                        Problem(
                            path,
                            "broken_reference",
                            f"O arquivo referenciado não existe: {target}",
                            line=number,
                        )
                    )

    # Segredos (todos os arquivos, inclusive scripts)
    for path, content in sorted(entries.items()):
        for number, label in find_secrets(content):
            problems.append(
                Problem(
                    path,
                    "secret_detected",
                    f"Parece haver {label} escrito no texto. "
                    "Remova e use as credenciais da empresa",
                    line=number,
                )
            )

    problems.extend(validate_manifest(manifest, tool_ids))
    return problems


def has_errors(problems: list[Problem]) -> bool:
    return any(p.severity == "error" for p in problems)


# ----------------------------------------------------------- skill do usuário


def validate_user_skill(
    content: str, expected_name: str | None = None
) -> tuple[list[Problem], dict]:
    """Valida um SKILL.md pessoal. Devolve (problemas, frontmatter)."""
    return _validate_loose_skill(content, expected_name, company=False)


def validate_company_skill(
    content: str, expected_name: str | None = None
) -> tuple[list[Problem], dict]:
    """Valida um SKILL.md da empresa (nome sem o prefixo u-, reservado às pessoais)."""
    return _validate_loose_skill(content, expected_name, company=True)


def _validate_loose_skill(
    content: str, expected_name: str | None, *, company: bool
) -> tuple[list[Problem], dict]:
    problems: list[Problem] = []
    path = "SKILL.md"
    if size_bytes(content) > MAX_USER_SKILL_BYTES:
        problems.append(Problem(path, "file_too_large", "A skill passa de 32 KiB"))
    if "\x00" in content:
        problems.append(Problem(path, "bad_encoding", "Arquivo binário ou com byte nulo"))
    data, _body, err = split_frontmatter(content)
    if err or data is None:
        problems.append(
            Problem(
                path,
                "bad_frontmatter",
                err or "A skill precisa de um cabeçalho com nome e descrição",
                line=1,
            )
        )
        return problems, {}
    name = data.get("name")
    name_re = SKILL_NAME if company else USER_SKILL_NAME
    if not isinstance(name, str) or len(name) > MAX_NAME or not name_re.match(name):
        message = (
            "Nome inválido: use letras minúsculas, números, ponto, hífen ou sublinhado "
            "(até 64 caracteres)"
            if company
            else "O nome de uma skill pessoal deve começar com u-"
        )
        problems.append(Problem(path, "bad_name", message, line=1))
    elif company and name.startswith("u-"):
        problems.append(
            Problem(path, "bad_name", "O prefixo u- é reservado às skills pessoais", line=1)
        )
    elif expected_name and name != expected_name:
        problems.append(Problem(path, "bad_name", "O nome da skill não pode ser alterado", line=1))
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        problems.append(Problem(path, "bad_frontmatter", "Falta o campo description", line=1))
    elif len(description) > MAX_DESCRIPTION:
        problems.append(
            Problem(path, "bad_frontmatter", "A descrição passa de 1024 caracteres", line=1)
        )
    for number, label in find_secrets(content):
        problems.append(
            Problem(
                path,
                "secret_detected",
                f"Parece haver {label} escrito no texto. Remova antes de salvar",
                line=number,
            )
        )
    return problems, data


def initial_package() -> tuple[list[dict], dict]:
    agent = (
        "# Quem você é\n\n"
        "Você é o assistente desta empresa. Responda em português, de forma clara e objetiva.\n\n"
        "## Como trabalhar\n\n"
        "- Entenda o pedido antes de agir; se faltar informação, pergunte.\n"
        "- Use as skills da pasta `skills/` quando o assunto combinar com a descrição delas.\n"
        "- Antes de qualquer ação que envolva dinheiro, envio de mensagens ou alteração de "
        "dados, peça confirmação.\n"
    )
    return [{"path": "AGENTE.md", "content": agent}], default_manifest()
