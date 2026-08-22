"""Executable checks for the tenant-code wrapper used by Custom Tool Runner.

The runner itself depends on MCP packages which are only installed in its
container image.  These tests deliberately extract the wrapper literal from
the production file and run it with the backend test interpreter, so the
important tenant-code boundary remains covered in the normal backend CI.
"""

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[2] / "custom_tool_runner" / "app.py"


def _wrapper() -> str:
    module = ast.parse(RUNNER.read_text(encoding="utf-8"))
    for statement in module.body:
        if (
            isinstance(statement, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_WRAPPER"
                for target in statement.targets
            )
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            return statement.value.value
    raise AssertionError("_WRAPPER não encontrado no Custom Tool Runner")


def _run(code: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="runner-sandbox-test-") as directory:
        root = Path(directory)
        (root / "runner.py").write_text(_wrapper(), encoding="utf-8")
        (root / "payload.json").write_text(
            json.dumps({"code": code, "inputs": {"value": 7}, "tenant_id": "tenant-a"}),
            encoding="utf-8",
        )
        return subprocess.run(
            [sys.executable, "-I", str(root / "runner.py"), str(root / "payload.json")],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )


def test_runner_wrapper_executes_a_valid_tenant_tool():
    result = _run("def main(inputs, context):\n    return {'value': inputs['value'] + 1}")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "success": True,
        "data": {"value": 8},
        "error": None,
    }


def test_runner_wrapper_blocks_unapproved_imports():
    result = _run("import os\ndef main(inputs, context):\n    return {'ok': True}")
    assert result.returncode != 0
    assert "nao permitido" in result.stderr


def test_runner_wrapper_blocks_metadata_network_destination():
    result = _run(
        "import requests\ndef main(inputs, context):\n"
        "    return {'status': requests.get('http://169.254.169.254').status_code}"
    )
    assert result.returncode != 0
    assert "bloqueado" in result.stderr


def test_runner_wrapper_blocks_ipv4_mapped_metadata_over_ipv6():
    """Mapped IPv6 must not become an alternate path to Cloud Run metadata."""
    result = _run(
        "import requests\ndef main(inputs, context):\n"
        "    return {'status': requests.get('http://[::ffff:169.254.169.254]').status_code}"
    )
    assert result.returncode != 0
    assert "bloqueado" in result.stderr
