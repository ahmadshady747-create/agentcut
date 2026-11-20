"""FFmpeg `-filter_complex_script` Builder & Safe Executor (v3.0.0).

Writing filtergraphs to a UTF-8 script file and invoking FFmpeg with
`-filter_complex_script` avoids OS command-line length limits (especially
Windows 8191-char limit with `run.bat`) and keeps complex multi-scene
documentary graphs clean and debuggable. Zero external dependencies.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Any


def write_filter_script(filter_graph: str, directory: str | None = None, prefix: str = "agentcut_fg_") -> str:
    """Persist a filter_complex graph to a UTF-8 `.ffscript` file and return its path."""
    target_dir = directory or tempfile.gettempdir()
    os.makedirs(target_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".ffscript", dir=target_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(filter_graph)
    return path


def run_ffmpeg_with_script(
    inputs: list[str],
    filter_graph: str,
    output_args: list[str],
    output_path: str,
    timeout: int = 300,
    work_dir: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Execute FFmpeg passing `filter_graph` via `-filter_complex_script`."""
    script_path = write_filter_script(filter_graph, directory=work_dir)
    try:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-v", "error",
            *inputs,
            "-filter_complex_script", script_path,
            *output_args,
            output_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0 or not os.path.isfile(output_path):
            return False, {
                "code": "FFMPEG_SCRIPT_FAILED",
                "returncode": proc.returncode,
                "stderr": (proc.stderr or "").strip()[-800:],
            }
        return True, {
            "output": output_path,
            "size_bytes": os.path.getsize(output_path),
            "filter_script_bytes": len(filter_graph.encode("utf-8")),
        }
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass
