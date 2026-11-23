"""Tests for AgentCut v3.3.13 — Cycle 12: Optional YouTube Data API v3 Resumable Publisher."""

from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from email.message import Message
from unittest import mock

from agentcut.__main__ import main as cli_main
from agentcut.server import TOOLS, call_tool
from agentcut.youtube_publisher import (
    extract_youtube_metadata_from_brief,
    publish_video_to_youtube,
    run_local_oauth_flow,
)


class _FakeHeaders(dict):
    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


class _FakeHTTPResponse:
    def __init__(self, body_dict: dict | None = None, headers: dict | None = None, status: int = 200):
        self._raw = json.dumps(body_dict or {}).encode("utf-8") if body_dict is not None else b""
        self.headers = _FakeHeaders(headers or {})
        self.status = status

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestV3313YouTubePublisher(unittest.TestCase):
    def test_metadata_extraction_from_brief_and_privacy_validation(self):
        brief = {
            "title": "رحلة الذكاء الاصطناعي في المونتاج الوثائقي",
            "tags": ["ذكاء اصطناعي", "مونتاج", "AgentCut"],
            "scenes": [
                {"id": "s1", "text": "في هذا الوثائقي نستعرض تطور أدوات المونتاج."},
                {"id": "s2", "text": "وكيف تعمل بدون أي اعتماديات خارجية."},
            ],
        }
        meta = extract_youtube_metadata_from_brief(brief)
        self.assertEqual(meta["title"], "رحلة الذكاء الاصطناعي في المونتاج الوثائقي")
        self.assertIn("في هذا الوثائقي", meta["description"])
        self.assertIn("بدون أي اعتماديات خارجية", meta["description"])
        self.assertEqual(meta["tags"], ["ذكاء اصطناعي", "مونتاج", "AgentCut"])
        # Default privacy is strictly 'private'
        self.assertEqual(meta["privacy_status"], "private")

        with self.assertRaises(ValueError):
            extract_youtube_metadata_from_brief(brief, privacy_status="friends_only")

    def test_confirm_publish_guardrail_blocks_unconfirmed_uploads(self):
        brief = {"title": "فيديو تجريبي", "scenes": [{"text": "مشهد أول"}]}
        ok, res = publish_video_to_youtube(
            video_path="/nonexistent/video.mp4",
            brief=brief,
            confirm_publish=False,
        )
        self.assertFalse(ok)
        self.assertFalse(res["published"])
        self.assertEqual(res["code"], "CONFIRMATION_REQUIRED")
        self.assertEqual(res["metadata_preview"]["title"], "فيديو تجريبي")
        self.assertEqual(res["metadata_preview"]["privacy_status"], "private")

    def test_resumable_chunked_upload_with_308_resume_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp4 = os.path.join(tmp, "master.mp4")
            # Create a 700 KiB dummy video so two 256 KiB + tail chunks are uploaded
            payload_bytes = b"0123456789ABCDEF" * (45 * 1024)
            with open(mp4, "wb") as f:
                f.write(payload_bytes)

            brief = {
                "title": "وثائقي اختبار الرفع",
                "tags": ["وثائقي", "اختبار"],
                "scenes": [{"text": "نص المشهد الأول للوصف."}],
            }

            calls = []

            def _fake_urlopen(req, timeout=60):
                calls.append(req)
                if "uploadType=resumable" in req.full_url:
                    return _FakeHTTPResponse(
                        {},
                        headers={"Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=session_abc"},
                    )
                # Chunk upload PUT
                content_range = req.get_header("Content-range") or ""
                total_len = len(payload_bytes)
                if not content_range.endswith(f"-{total_len - 1}/{total_len}"):
                    # Simulate HTTP 308 Resume Incomplete for non-final chunks
                    end_byte = content_range.split("/")[0].split("-")[-1]
                    hdrs = Message()
                    hdrs["Range"] = f"bytes=0-{end_byte}"
                    raise urllib.error.HTTPError(
                        req.full_url, 308, "Resume Incomplete", hdrs, io.BytesIO(b"")
                    )
                return _FakeHTTPResponse({"id": "YT_VID_998877", "status": {"privacyStatus": "unlisted"}})

            with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                ok, res = publish_video_to_youtube(
                    video_path=mp4,
                    brief=brief,
                    privacy_status="unlisted",
                    confirm_publish=True,
                    access_token="ya29.test_token",
                    chunk_size=256 * 1024,
                )

            self.assertTrue(ok)
            self.assertTrue(res["published"])
            self.assertEqual(res["video_id"], "YT_VID_998877")
            self.assertEqual(res["url"], "https://www.youtube.com/watch?v=YT_VID_998877")
            self.assertEqual(res["privacy_status"], "unlisted")
            self.assertEqual(res["chunks_uploaded"], 3)
            self.assertEqual(res["bytes_uploaded"], len(payload_bytes))
            # 1 session init POST + 3 chunk PUTs
            self.assertEqual(len(calls), 4)
            self.assertEqual(calls[0].get_method(), "POST")
            self.assertEqual(calls[1].get_method(), "PUT")

    def test_local_oauth_loopback_server_captures_code_and_exchanges_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            tok_file = os.path.join(tmp, "yt_token.json")
            # Bind ephemeral port by finding a free port first
            import socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                free_port = s.getsockname()[1]

            def _trigger_callback():
                import time as _t
                _t.sleep(0.15)
                cb_url = f"http://127.0.0.1:{free_port}/oauth2callback?code=auth_code_xyz"
                with urllib.request.urlopen(cb_url, timeout=5) as r:
                    r.read()

            t = threading.Thread(target=_trigger_callback, daemon=True)
            t.start()

            with mock.patch(
                "agentcut.youtube_publisher.exchange_code_for_token",
                return_value={"access_token": "ya29.loopback_ok", "refresh_token": "1//refresh_ok", "expires_in": 3600},
            ) as m_ex:
                tok_data = run_local_oauth_flow(
                    client_id="cid_123",
                    client_secret="csec_456",
                    port=free_port,
                    open_browser=False,
                    token_save_path=tok_file,
                )
            t.join(timeout=3)
            self.assertEqual(m_ex.call_count, 1)
            self.assertEqual(tok_data["access_token"], "ya29.loopback_ok")
            self.assertTrue(os.path.isfile(tok_file))

    def test_mcp_tool_73_and_cli_youtube_publish(self):
        names = [t["name"] for t in TOOLS]
        self.assertIn("publish_youtube_video", names)
        self.assertEqual(len(TOOLS), 73)

        # Calling without confirm_publish=True returns structured CONFIRMATION_REQUIRED error
        resp_unconfirmed = call_tool("publish_youtube_video", {
            "video_path": "/tmp/demo.mp4",
            "title": "تجربة بدون تأكيد",
        })
        self.assertTrue(resp_unconfirmed.get("isError", False))
        err_obj = json.loads(resp_unconfirmed["content"][0]["text"])
        self.assertEqual(err_obj["code"], "CONFIRMATION_REQUIRED")

        # CLI without --confirm returns non-zero and prints confirmation preview
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = cli_main(["youtube-publish", "/tmp/demo.mp4", "--title", "عنوان تجريبي"])
        self.assertEqual(rc, 1)
        cli_out = json.loads(buf.getvalue())
        self.assertEqual(cli_out["code"], "CONFIRMATION_REQUIRED")


if __name__ == "__main__":
    unittest.main()
