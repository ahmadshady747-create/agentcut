"""Optional YouTube Data API v3 Resumable Publisher for AgentCut (v3.3.13).

Zero external Python dependencies (Python stdlib only: urllib, http.server,
json, os, time, webbrowser).

Design & Safety Guardrails:
1. Never publishes by default: ``confirm_publish=True`` is strictly required
   on every publish call. When ``confirm_publish=False`` (default), the call
   refuses to upload and returns a structured ``CONFIRMATION_REQUIRED`` payload
   with the extracted metadata preview so the user can review before approving.
2. Reads ``title``, ``description``, ``tags``, ``category_id``, and
   ``privacy_status`` (``"private"`` default, ``"unlisted"``, or ``"public"``)
   directly from an AgentCut brief/project JSON or explicit overrides.
3. Implements local loopback OAuth 2.0 authorization via ``http.server`` and
   token refresh via ``urllib.request``, plus chunked Resumable Upload
   (``uploadType=resumable``) suitable for large 180MB+ 1080p master MP4s.
"""

from __future__ import annotations

import http.server
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Any

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
YOUTUBE_RESUMABLE_INIT_URL = (
    "https://www.googleapis.com/upload/youtube/v3/videos"
    "?uploadType=resumable&part=snippet,status"
)

VALID_PRIVACY_STATUSES = ("private", "unlisted", "public")
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB (multiple of 256 KiB per YouTube API spec)


def _default_token_path() -> str:
    home = os.environ.get("AGENTCUT_HOME") or os.path.expanduser("~/.agentcut")
    return os.path.join(home, "youtube_oauth_token.json")


def extract_youtube_metadata_from_brief(
    brief: dict[str, Any] | None = None,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    privacy_status: str | None = None,
    category_id: str | None = None,
    default_language: str | None = None,
) -> dict[str, Any]:
    """Derive YouTube snippet and status metadata from an AgentCut brief + overrides."""
    b = brief if isinstance(brief, dict) else {}
    yt_cfg = b.get("youtube") if isinstance(b.get("youtube"), dict) else {}

    # 1. Title (YouTube max 100 chars)
    raw_title = (
        title
        or yt_cfg.get("title")
        or b.get("title")
        or "AgentCut Documentary"
    )
    clean_title = " ".join(str(raw_title).strip().split())[:100] or "AgentCut Documentary"

    # 2. Description (combine brief description/summary + scene narration lines)
    raw_desc = description if description is not None else yt_cfg.get("description") or b.get("description")
    if not raw_desc:
        lines: list[str] = []
        for sc in (b.get("scenes") or []):
            if not isinstance(sc, dict):
                continue
            txt = str(sc.get("text") or sc.get("script") or "").strip()
            if txt:
                lines.append(txt)
        raw_desc = "\n\n".join(lines) if lines else clean_title
    clean_desc = str(raw_desc).strip()[:5000]

    # 3. Tags
    raw_tags = tags if tags is not None else yt_cfg.get("tags") or b.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
    clean_tags: list[str] = []
    seen_tags: set[str] = set()
    for t in (raw_tags if isinstance(raw_tags, list) else []):
        ts = str(t).strip()
        if ts and ts.lower() not in seen_tags:
            seen_tags.add(ts.lower())
            clean_tags.append(ts[:100])
        if len(clean_tags) >= 30:
            break

    # 4. Privacy status (default strictly to 'private' for safety)
    priv = str(
        privacy_status
        or yt_cfg.get("privacy_status")
        or b.get("privacy_status")
        or "private"
    ).strip().lower()
    if priv not in VALID_PRIVACY_STATUSES:
        raise ValueError(
            f"Invalid privacy_status {priv!r}; must be one of {VALID_PRIVACY_STATUSES}."
        )

    cat_id = str(
        category_id
        or yt_cfg.get("category_id")
        or b.get("category_id")
        or "27"  # 27 = Education / Documentary default
    ).strip()

    lang = str(
        default_language
        or yt_cfg.get("language")
        or b.get("language")
        or "ar"
    ).strip()

    return {
        "title": clean_title,
        "description": clean_desc,
        "tags": clean_tags,
        "category_id": cat_id,
        "privacy_status": priv,
        "default_language": lang,
        "self_declared_made_for_kids": bool(yt_cfg.get("made_for_kids", False)),
    }


def exchange_code_for_token(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    token_endpoint: str = GOOGLE_TOKEN_ENDPOINT,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Exchange an OAuth 2.0 authorization code for an access/refresh token dict."""
    form = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")
    req = urllib.request.Request(
        token_endpoint,
        data=form,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if "expires_in" in payload:
        payload["expires_at"] = int(time.time()) + int(payload["expires_in"]) - 30
    return payload


def refresh_oauth_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    token_endpoint: str = GOOGLE_TOKEN_ENDPOINT,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Refresh an OAuth 2.0 access token using a stored refresh_token via stdlib urllib."""
    form = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode("utf-8")
    req = urllib.request.Request(
        token_endpoint,
        data=form,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if "refresh_token" not in payload:
        payload["refresh_token"] = refresh_token
    if "expires_in" in payload:
        payload["expires_at"] = int(time.time()) + int(payload["expires_in"]) - 30
    return payload


def run_local_oauth_flow(
    client_id: str,
    client_secret: str,
    port: int = 8765,
    timeout: float = 120.0,
    open_browser: bool = True,
    token_save_path: str | None = None,
) -> dict[str, Any]:
    """Run a local loopback OAuth 2.0 flow using Python stdlib ``http.server``."""
    captured: dict[str, str | None] = {"code": None, "error": None}

    class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if "code" in qs and qs["code"]:
                captured["code"] = qs["code"][0]
                body = b"<html><body><h2>AgentCut YouTube OAuth complete. You may close this tab.</h2></body></html>"
                self.send_response(200)
            else:
                captured["error"] = qs.get("error", ["missing_code"])[0]
                body = b"<html><body><h2>AgentCut YouTube OAuth failed.</h2></body></html>"
                self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", port), _OAuthCallbackHandler)
    server.timeout = timeout
    actual_port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{actual_port}/oauth2callback"

    auth_qs = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": YOUTUBE_UPLOAD_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    })
    auth_url = f"{GOOGLE_AUTH_ENDPOINT}?{auth_qs}"
    if open_browser:
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

    try:
        server.handle_request()
    finally:
        server.server_close()

    if captured["error"] or not captured["code"]:
        raise RuntimeError(f"OAuth callback did not return a code (error={captured['error']!r}).")

    token_data = exchange_code_for_token(
        client_id=client_id,
        client_secret=client_secret,
        code=str(captured["code"]),
        redirect_uri=redirect_uri,
    )
    token_data["client_id"] = client_id
    token_data["client_secret"] = client_secret

    save_p = token_save_path or _default_token_path()
    os.makedirs(os.path.dirname(save_p), exist_ok=True)
    with open(save_p, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)
    return token_data


def resolve_access_token(
    access_token: str | None = None,
    token_path: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> str:
    """Resolve a valid YouTube OAuth access token from direct arg, env, or cached JSON."""
    direct = (access_token or os.environ.get("YOUTUBE_ACCESS_TOKEN") or "").strip()
    if direct:
        return direct

    t_path = token_path or os.environ.get("YOUTUBE_TOKEN_PATH") or _default_token_path()
    if os.path.isfile(t_path):
        with open(t_path, "r", encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            tok = str(stored.get("access_token") or "").strip()
            exp = stored.get("expires_at")
            now = int(time.time())
            if tok and (not isinstance(exp, (int, float)) or exp > now):
                return tok
            ref = str(stored.get("refresh_token") or "").strip()
            cid = (client_id or stored.get("client_id") or os.environ.get("YOUTUBE_CLIENT_ID") or "").strip()
            csec = (client_secret or stored.get("client_secret") or os.environ.get("YOUTUBE_CLIENT_SECRET") or "").strip()
            if ref and cid and csec:
                refreshed = refresh_oauth_token(cid, csec, ref)
                stored.update(refreshed)
                with open(t_path, "w", encoding="utf-8") as f:
                    json.dump(stored, f, indent=2)
                return str(refreshed["access_token"])

    raise ValueError(
        "No valid YouTube OAuth access token found. Pass access_token, set YOUTUBE_ACCESS_TOKEN, "
        "or authenticate locally via run_local_oauth_flow()."
    )


def initiate_resumable_upload(
    access_token: str,
    metadata: dict[str, Any],
    file_size: int,
    mime_type: str = "video/mp4",
    init_url: str = YOUTUBE_RESUMABLE_INIT_URL,
    timeout: float = 30.0,
) -> str:
    """Start a YouTube Data API v3 resumable upload session and return the upload ``Location`` URL."""
    body_obj = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": metadata.get("tags", []),
            "categoryId": str(metadata.get("category_id", "27")),
            "defaultLanguage": str(metadata.get("default_language", "ar")),
        },
        "status": {
            "privacyStatus": metadata["privacy_status"],
            "selfDeclaredMadeForKids": bool(metadata.get("self_declared_made_for_kids", False)),
        },
    }
    body_bytes = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        init_url,
        data=body_bytes,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(file_size),
            "X-Upload-Content-Type": mime_type,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            loc = resp.headers.get("Location") or resp.headers.get("location")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"YouTube resumable init HTTP {exc.code}: {detail}") from exc

    if not loc:
        raise RuntimeError("YouTube resumable init response did not include a Location header.")
    return str(loc)


def upload_video_resumable(
    upload_url: str,
    video_path: str,
    access_token: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    mime_type: str = "video/mp4",
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Stream a video file in resumable chunks to the YouTube upload session URL."""
    total_size = os.path.getsize(video_path)
    if total_size <= 0:
        raise ValueError(f"Video file is empty: {video_path!r}")

    eff_chunk = max(256 * 1024, int(chunk_size))
    offset = 0
    chunks_sent = 0
    final_payload: dict[str, Any] = {}

    with open(video_path, "rb") as vf:
        while offset < total_size:
            data = vf.read(eff_chunk)
            if not data:
                break
            end_byte = offset + len(data) - 1
            req = urllib.request.Request(
                upload_url,
                data=data,
                method="PUT",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Length": str(len(data)),
                    "Content-Type": mime_type,
                    "Content-Range": f"bytes {offset}-{end_byte}/{total_size}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace").strip()
                    if raw:
                        final_payload = json.loads(raw)
                    offset = end_byte + 1
                    chunks_sent += 1
            except urllib.error.HTTPError as exc:
                if exc.code == 308:
                    # 308 Resume Incomplete — standard YouTube intermediate chunk response
                    range_hdr = exc.headers.get("Range") if exc.headers else None
                    if range_hdr and "-" in range_hdr:
                        try:
                            last_received = int(range_hdr.split("-")[-1])
                            offset = last_received + 1
                            vf.seek(offset)
                        except ValueError:
                            offset = end_byte + 1
                    else:
                        offset = end_byte + 1
                    chunks_sent += 1
                    continue
                detail = exc.read().decode("utf-8", errors="replace")[:400]
                raise RuntimeError(f"YouTube chunk upload HTTP {exc.code}: {detail}") from exc

    final_payload["_chunks_uploaded"] = chunks_sent
    final_payload["_bytes_uploaded"] = total_size
    return final_payload


def publish_video_to_youtube(
    video_path: str,
    brief: dict[str, Any] | None = None,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    privacy_status: str | None = None,
    category_id: str | None = None,
    confirm_publish: bool = False,
    access_token: str | None = None,
    token_path: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> tuple[bool, dict[str, Any]]:
    """Publish a rendered MP4 to YouTube via YouTube Data API v3 resumable upload.

    Safety rule: Never uploads unless ``confirm_publish=True`` is explicitly passed.
    """
    try:
        meta = extract_youtube_metadata_from_brief(
            brief=brief,
            title=title,
            description=description,
            tags=tags,
            privacy_status=privacy_status,
            category_id=category_id,
        )
    except ValueError as exc:
        return False, {"code": "INVALID_METADATA", "message": str(exc)}

    if not confirm_publish:
        return False, {
            "code": "CONFIRMATION_REQUIRED",
            "published": False,
            "message": (
                "Publishing to YouTube is an external action and is disabled by default. "
                "Review the metadata preview and re-call with confirm_publish=True to upload."
            ),
            "video_path": video_path,
            "metadata_preview": meta,
        }

    if not video_path or not os.path.isfile(video_path):
        return False, {
            "code": "FILE_NOT_FOUND",
            "published": False,
            "message": f"Rendered video file not found: {video_path!r}",
        }

    try:
        token = resolve_access_token(access_token=access_token, token_path=token_path)
        file_size = os.path.getsize(video_path)
        upload_url = initiate_resumable_upload(
            access_token=token,
            metadata=meta,
            file_size=file_size,
        )
        yt_resp = upload_video_resumable(
            upload_url=upload_url,
            video_path=video_path,
            access_token=token,
            chunk_size=chunk_size,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        return False, {
            "code": "YOUTUBE_UPLOAD_ERROR",
            "published": False,
            "message": str(exc),
        }

    video_id = str(yt_resp.get("id") or "").strip()
    watch_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else None
    shorts_url = f"https://www.youtube.com/shorts/{video_id}" if video_id else None

    return True, {
        "published": True,
        "video_id": video_id,
        "url": watch_url,
        "shorts_url": shorts_url,
        "privacy_status": meta["privacy_status"],
        "title": meta["title"],
        "tags": meta["tags"],
        "bytes_uploaded": yt_resp.get("_bytes_uploaded", file_size),
        "chunks_uploaded": yt_resp.get("_chunks_uploaded", 1),
    }
