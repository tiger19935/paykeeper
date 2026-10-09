"""Assert the money paths never use `float`.

Scanning the AST (not just grepping strings) means a comment or docstring
mentioning floats will not fail the test — only an actual `float` call,
`float` identifier reference, or float literal in code counts.
"""

from __future__ import annotations

import ast
from pathlib import Path

MONEY_PATHS = [
    Path(__file__).resolve().parents[2] / "src" / "paykeeper" / "domain",
    Path(__file__).resolve().parents[2] / "src" / "paykeeper" / "ledger",
    Path(__file__).resolve().parents[2] / "src" / "paykeeper" / "providers",
]


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for root in MONEY_PATHS:
        if root.exists():
            files.extend(root.rglob("*.py"))
    return files


def test_no_float_in_money_paths() -> None:
    files = _iter_py_files()
    assert files, "money paths produced no Python files to scan"

    offenders: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                offenders.append(f"{path}:{node.lineno} float literal {node.value!r}")
            elif isinstance(node, ast.Name) and node.id == "float":
                offenders.append(f"{path}:{node.lineno} float name reference")

    assert not offenders, "float used in money path:\n  " + "\n  ".join(offenders)
