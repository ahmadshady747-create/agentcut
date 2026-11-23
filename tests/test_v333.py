"""Verification tests for AgentCut v3.3.3 (Cycle 3 Real Technical Code, KPI & Chart Visual Cards)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentcut import __version__
from agentcut.__main__ import main as cli_main
from agentcut.render import produce_explainer
from agentcut.server import TOOLS, call_tool
from agentcut.tech_cards import (
    auto_attach_tech_cards,
    render_bar_chart_card_png,
    render_code_card_png,
    render_kpi_card_png,
)


class TestCycle3TechCardsV333(unittest.TestCase):
    def test_render_code_kpi_and_chart_pngs(self):
        with tempfile.TemporaryDirectory() as td:
            c_png = render_code_card_png(
                ['{"style": "editorial", "deps": 0}'],
                output_path=str(Path(td) / "code.png"),
                title="project.json",
                subtitle="ملف واحد فيه كل شيء",
                width=540,
                height=960,
            )
            self.assertTrue(os.path.isfile(c_png))
            self.assertGreater(os.path.getsize(c_png), 1024)

            k_png = render_kpi_card_png(
                value="64",
                label="أداة MCP يطلبها الوكيل",
                subtitle="صفر اعتماديات خارجية",
                output_path=str(Path(td) / "kpi.png"),
                width=540,
                height=960,
            )
            self.assertTrue(os.path.isfile(k_png))
            self.assertGreater(os.path.getsize(k_png), 1024)

            b_png = render_bar_chart_card_png(
                [{"label": "v1.64", "value": 34}, {"label": "v3.3", "value": 64}],
                title="تطور أدوات AgentCut",
                output_path=str(Path(td) / "chart.png"),
                width=540,
                height=960,
            )
            self.assertTrue(os.path.isfile(b_png))
            self.assertGreater(os.path.getsize(b_png), 1024)

    def test_produce_explainer_auto_generates_tech_cards_for_code_and_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            wav1 = Path(td) / "s1.wav"
            wav2 = Path(td) / "s2.wav"
            for wav in (wav1, wav2):
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                     "-i", "sine=frequency=340:duration=2.000", str(wav)],
                    check=True, timeout=20,
                )
            srt1 = Path(td) / "s1.srt"
            srt2 = Path(td) / "s2.srt"
            srt1.write_text("1\n00:00:00,000 --> 00:00:02,000\nاسمه بروجيكت جيسون ملف واحد فيه كل شيء\n", encoding="utf-8")
            srt2.write_text("1\n00:00:00,000 --> 00:00:02,000\n64 أداة يطلبها الوكيل مباشرة\n", encoding="utf-8")

            # Notice: NO broll image paths supplied — produce_explainer auto-renders real tech cards!
            brief = {
                "title": "Auto Tech Cards Proof",
                "style": "editorial",
                "aspect": "9:16",
                "render_profile": "proxy",
                "scenes": [
                    {
                        "id": "sc1",
                        "text": "اسمه بروجيكت جيسون ملف واحد فيه كل شيء",
                        "narration": str(wav1),
                        "captions": str(srt1),
                    },
                    {
                        "id": "sc2",
                        "text": "64 أداة يطلبها الوكيل مباشرة",
                        "narration": str(wav2),
                        "captions": str(srt2),
                    },
                ],
            }
            out_mp4 = str(Path(td) / "tech_editorial.mp4")
            ok, info = produce_explainer(brief, out_mp4)
            self.assertTrue(ok, info)
            self.assertEqual(info.get("tech_cards_generated"), 2)
            self.assertTrue(os.path.isfile(out_mp4))

    def test_64_mcp_tools_and_cli_tech_card(self):
        self.assertEqual(__version__, "3.3.13")
        self.assertEqual(len(TOOLS), 73)
        with tempfile.TemporaryDirectory() as td:
            out_p = str(Path(td) / "mcp_kpi.png")
            r = call_tool("render_tech_card", {"kind": "kpi", "value": "576", "label": "اختبار", "output": out_p, "width": 540, "height": 960})
            self.assertFalse(r.get("isError"))
            self.assertTrue(os.path.isfile(out_p))

            cli_p = str(Path(td) / "cli_code.png")
            self.assertEqual(cli_main(["tech-card", "code", cli_p]), 0)
            self.assertTrue(os.path.isfile(cli_p))


if __name__ == "__main__":
    unittest.main()
