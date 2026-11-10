"""JSON formatting, token-efficient minification, and precision syntax diagnosis.

Inspired by ProCraft's JSON validator and formatter, adapted for AgentCut MCP tool
token savings and structured syntax error remediation. Zero external dependencies.
"""

from __future__ import annotations

import json
from typing import Any


def minify_json(data: str | dict[str, Any] | list[Any]) -> str:
    """Minify JSON into a single compact line without losing precision.

    Reduces token usage up to 40% when transferring large AgentCut project specs over MCP/LLM contexts.
    """
    if isinstance(data, str):
        parsed = json.loads(data)
    else:
        parsed = data
    return json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)


def format_json(data: str | dict[str, Any] | list[Any], indent: int = 2) -> str:
    """Format and pretty-print JSON with consistent indentation and UTF-8 preserve."""
    if isinstance(data, str):
        parsed = json.loads(data)
    else:
        parsed = data
    return json.dumps(parsed, indent=indent, ensure_ascii=False)


def validate_json_syntax(raw_text: str) -> dict[str, Any]:
    """Validate JSON text and report syntax errors with precise line, column, and visual pointer.

    Provides actionable remediation hints (trailing comma, unquoted key, unclosed string, etc.).
    """
    if not raw_text.strip():
        return {
            "valid": False,
            "error": "Empty input string",
            "line": 1,
            "column": 1,
            "snippet": "",
            "fix_hint": "Provide a valid non-empty JSON string.",
        }

    try:
        parsed = json.loads(raw_text)
        return {
            "valid": True,
            "parsed": parsed,
            "error": None,
            "line": None,
            "column": None,
            "snippet": None,
            "fix_hint": None,
        }
    except json.JSONDecodeError as exc:
        lines = raw_text.splitlines()
        line_idx = max(0, min(len(lines) - 1, exc.lineno - 1))
        offending_line = lines[line_idx] if lines else ""
        col = exc.colno

        # Build visual caret pointer
        pointer = " " * max(0, col - 1) + "^"
        snippet = f"Line {exc.lineno}:\n  {offending_line}\n  {pointer}"

        # Heuristic fix hints
        msg = str(exc.msg).lower()
        fix_hint = "Check JSON syntax around the indicated position."
        prev_line = lines[line_idx - 1] if line_idx > 0 else ""
        if "expecting property name" in msg:
            if offending_line.strip().endswith(",") or prev_line.strip().endswith(","):
                fix_hint = "Remove the trailing comma before closing brace or bracket."
            else:
                fix_hint = "Ensure all object keys are enclosed in double quotes (e.g. \"key\": value)."
        elif "delimiter" in msg or "expecting ':'" in msg:
            fix_hint = "Ensure keys and values are separated by a colon ':'."
        elif "unterminated string" in msg:
            fix_hint = "Close open string literal with a matching double quote '\"'."
        elif "extra data" in msg:
            fix_hint = "Ensure there is only one root JSON object or array."

        return {
            "valid": False,
            "error": exc.msg,
            "line": exc.lineno,
            "column": exc.colno,
            "snippet": snippet,
            "fix_hint": fix_hint,
        }
