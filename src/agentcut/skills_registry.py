"""Bundled AgentCut Skills Registry & Exporter (v3.2.0).

Discovers, serves, and exports AgentCut's modular `skills/*/SKILL.md` files so
any connected MCP agent or CLI user can load focused workflows on demand or
install them into an external agent's skills directory. Zero dependencies.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def get_skills_root() -> Path:
    """Return the root `skills/` directory bundled with AgentCut."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root / "skills"


def _parse_frontmatter(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return meta
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta


def list_agentcut_skills(skills_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """List all bundled AgentCut skills with parsed name, description, and path."""
    root = Path(skills_dir) if skills_dir else get_skills_root()
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir()):
        skill_md = entry / "SKILL.md"
        if entry.is_dir() and skill_md.is_file():
            raw = skill_md.read_text(encoding="utf-8")
            meta = _parse_frontmatter(raw)
            out.append({
                "name": meta.get("name", entry.name),
                "description": meta.get("description", ""),
                "path": str(skill_md),
                "lines": len(raw.splitlines()),
            })
    return out


def get_agentcut_skill(name: str, skills_dir: str | Path | None = None) -> dict[str, Any] | None:
    """Load the full SKILL.md content for a bundled AgentCut skill by name."""
    target = str(name or "").strip().lower()
    if not target:
        return None
    for item in list_agentcut_skills(skills_dir=skills_dir):
        if item["name"].lower() == target or Path(item["path"]).parent.name.lower() == target:
            raw = Path(item["path"]).read_text(encoding="utf-8")
            return {
                "name": item["name"],
                "description": item["description"],
                "path": item["path"],
                "content": raw,
            }
    return None


def export_agentcut_skills(dest_dir: str | Path, skills_dir: str | Path | None = None) -> dict[str, Any]:
    """Copy all bundled AgentCut skill folders into a target agent's skills directory."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    exported: list[str] = []
    for item in list_agentcut_skills(skills_dir=skills_dir):
        src_folder = Path(item["path"]).parent
        dst_folder = dest / src_folder.name
        if dst_folder.exists():
            shutil.rmtree(dst_folder)
        shutil.copytree(src_folder, dst_folder)
        exported.append(item["name"])
    return {
        "destination": str(dest),
        "exported_count": len(exported),
        "skills": exported,
    }
