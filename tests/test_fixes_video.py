import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from agentcut.validate import find_font_for_text, validate_project, contains_arabic, contains_latin
from agentcut.render import _explainer_pacing_beats, _diagram_derived_texts
from agentcut.tts import auto_diacritize_arabic, generate_ambient_audio


class TestVideoDefectsFixes(unittest.TestCase):
    def test_mixed_script_font_selection(self):
        """Mixed Arabic + Latin text selects a dual-script font to prevent tofu boxes."""
        mixed = "صراع العمالقة: Google و OpenAI و Anthropic"
        font = find_font_for_text(mixed)
        self.assertIsNotNone(font)
        basename = os.path.basename(font).lower()
        self.assertTrue(
            any(k in basename for k in ["segoe", "arial", "tahoma", "cairo"]),
            f"Expected dual-script font for mixed text, got: {basename}"
        )

        pure_ar = "صراع العمالقة"
        font_ar = find_font_for_text(pure_ar)
        self.assertIn("notosansarabic", os.path.basename(font_ar).lower())

        pure_lat = "Google and OpenAI"
        font_lat = find_font_for_text(pure_lat)
        self.assertIn("dejavu", os.path.basename(font_lat).lower())

    def test_timeline_and_process_flow_labels_validation(self):
        """Diagram templates timeline and process_flow accept labels parameter."""
        project = {
            "version": 1,
            "id": "test_labels",
            "output": {"width": 1280, "height": 720, "fps": 25},
            "clips": [{"id": "c1", "kind": "image", "src": "data/ai_race_bg.png", "duration": 10.0, "track": 0}],
            "diagrams": [
                {
                    "id": "d1",
                    "start": 0.0,
                    "end": 8.0,
                    "template": "timeline",
                    "params": {"steps": 3, "labels": ["Step 1", "Step 2", "Step 3"]},
                    "size": 0.5,
                },
                {
                    "id": "d2",
                    "start": 1.0,
                    "end": 7.0,
                    "template": "process_flow",
                    "params": {"steps": 2, "labels": ["Input", "Output"]},
                    "size": 0.4,
                }
            ]
        }
        errors, info = validate_project(project)
        self.assertEqual(errors, [], f"Validation errors: {errors}")
        self.assertEqual(len(info["diagrams"]), 2)

    def test_pacing_beats_no_subsecond_crash(self):
        """Pacing beats handle fractional durations without subsecond enter/exit crashes."""
        # A 40.31s scene previously created a 0.31s lower_third beat where enter+exit=1.2s crashed validation
        beats = _explainer_pacing_beats(
            start=0.0,
            dur=40.31,
            title="Scene Title",
            si=0,
            nscenes=5,
            width=1280,
            height=720,
        )
        self.assertTrue(len(beats) > 0)
        for b in beats:
            w = b["end"] - b["start"]
            self.assertGreaterEqual(w, 1.5, f"Beat window too small: {w}")
            if "enter" in b and "exit" in b:
                self.assertLessEqual(b["enter"] + b["exit"], w + 1e-4)

    def test_diagram_derived_texts_generation(self):
        """Diagram labels generate centered drawtext items inside diagram boxes."""
        info = {
            "diagrams": [
                {
                    "id": "dg1",
                    "template": "timeline",
                    "params": {"steps": 3, "labels": ["Google", "OpenAI", "Anthropic"]},
                    "start": 2.0,
                    "end": 10.0,
                    "draw": 2.0,
                    "size": 0.6,
                    "position": "center",
                }
            ]
        }
        derived = _diagram_derived_texts(info, 1280, 720)
        self.assertEqual(len(derived), 3)
        self.assertEqual(derived[0]["text"], "Google")
        self.assertEqual(derived[1]["text"], "OpenAI")
        self.assertEqual(derived[2]["text"], "Anthropic")
        for item in derived:
            self.assertEqual(item["start"], 4.0)
            self.assertEqual(item["end"], 10.0)

    def test_arabic_homographs_expansion(self):
        """Arabic homographs and tech names are accurately vowelized."""
        text = "شهد سباق الذكاء الاصطناعي معركة بين جوجل وأوبن إيه آي وأنثروبيك"
        vowelized = auto_diacritize_arabic(text)
        self.assertIn("شَهِدَ", vowelized)
        self.assertIn("الذَّكَاء", vowelized)
        self.assertIn("الاصْطِنَاعِيّ", vowelized)
        self.assertIn("جُوجِلْ", vowelized)
        self.assertIn("أَنْثُرُوبِيكْ", vowelized)


    def test_clean_captions_with_diacritized_speech(self):
        """Audio keeps vocalized tashkeel while subtitles and karaoke words are stripped for clean display."""
        from agentcut.tts import _synthesize_offline_fallback
        text = "مَعَ إِطْلَالَةِ عَامِ أَلْفَيْنِ وَسِتَّةٍ وَعِشْرِينَ"
        out = _synthesize_offline_fallback(text, "test_clean.mp3", "test_clean.srt", clean_captions=True)
        try:
            # Word timings must be stripped of diacritics
            words = [w["word"] for w in out["word_timings"]]
            self.assertEqual(words, ["مع", "إطلالة", "عام", "ألفين", "وستة", "وعشرين"])
            # SRT file must be stripped of diacritics
            with open("test_clean.srt", encoding="utf-8") as f:
                srt_content = f.read()
            self.assertIn("إطلالة", srt_content)
            self.assertNotIn("إِطْلَالَةِ", srt_content)
        finally:
            for p in ["test_clean.mp3", "test_clean.srt"]:
                if os.path.exists(p):
                    os.remove(p)


if __name__ == "__main__":
    unittest.main()
