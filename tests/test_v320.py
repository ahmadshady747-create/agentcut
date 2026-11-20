"""Verification suite for AgentCut v3.2.0 (Native Skills Pack & Registry)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agentcut import __version__
from agentcut.__main__ import main as cli_main
from agentcut.server import TOOLS, call_tool
from agentcut.skills_registry import (
    export_agentcut_skills,
    get_agentcut_skill,
    list_agentcut_skills,
)


class TestSkillsPackV32(unittest.TestCase):
    def test_four_bundled_skills_listed_and_loaded(self):
        skills = list_agentcut_skills()
        names = {s["name"] for s in skills}
        self.assertEqual(
            names,
            {
                "agentcut-autopilot",
                "agentcut-editorial-director",
                "agentcut-agentmedia-bridge",
                "agentcut-qc-healer",
            },
        )
        for s in skills:
            self.assertTrue(len(s["description"]) > 20)
            self.assertLess(s["lines"], 150)

        sk = get_agentcut_skill("agentcut-autopilot")
        self.assertIsNotNone(sk)
        self.assertIn("autopilot_produce", sk["content"])

    def test_export_skills_to_external_agent_dir(self):
        with tempfile.TemporaryDirectory() as td:
            res = export_agentcut_skills(td)
            self.assertEqual(res["exported_count"], 4)
            for name in res["skills"]:
                self.assertTrue((Path(td) / name / "SKILL.md").is_file())

    def test_62_mcp_tools_and_skills_cli(self):
        self.assertEqual(__version__, "3.2.0")
        self.assertEqual(len(TOOLS), 62)

        r1 = call_tool("list_agentcut_skills", {})
        self.assertFalse(r1.get("isError"))
        self.assertEqual(len(json.loads(r1["content"][0]["text"])["skills"]), 4)

        r2 = call_tool("get_agentcut_skill", {"name": "agentcut-editorial-director"})
        self.assertFalse(r2.get("isError"))
        self.assertIn("humanize_brief", json.loads(r2["content"][0]["text"])["content"])

        self.assertEqual(cli_main(["skills", "list"]), 0)
        self.assertEqual(cli_main(["skills", "show", "agentcut-qc-healer"]), 0)
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(cli_main(["skills", "export", td]), 0)
            self.assertTrue((Path(td) / "agentcut-autopilot" / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
