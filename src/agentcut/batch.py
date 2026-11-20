"""Multi-Project Batch Production Orchestrator (v2.0.0).

Executes a manifest of AgentCut jobs (`explainer`, `project`, `bundle`, or
`smart_cut`) with hardware-safe concurrency throttling, resume-if-done skipping,
per-job error isolation, optional post-render QC auditing, and a JSON report.
Zero external dependencies.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import time
from typing import Any

from .profiles import apply_profile_to_brief, detect_hardware_profile


def _load_json_if_path(item: Any) -> Any:
    if isinstance(item, str) and os.path.isfile(item):
        with open(item, encoding="utf-8") as fh:
            return json.load(fh)
    return item


def _execute_single_job(
    job: dict[str, Any],
    resume: bool,
    default_profile: str | None,
    qc_audit: bool,
) -> dict[str, Any]:
    job_id = str(job.get("id") or "job")
    kind = str(job.get("kind") or "explainer").strip().lower()
    output_path = str(job.get("output") or "")
    t0 = time.monotonic()

    if not output_path:
        return {
            "id": job_id,
            "kind": kind,
            "status": "failed",
            "error": "Missing 'output' path in batch job.",
            "elapsed_sec": 0.0,
        }

    # Resume mode: skip already-rendered valid outputs (>1KB)
    if resume and os.path.isfile(output_path) and os.path.getsize(output_path) > 1024:
        res: dict[str, Any] = {
            "id": job_id,
            "kind": kind,
            "output": output_path,
            "status": "skipped",
            "elapsed_sec": round(time.monotonic() - t0, 3),
        }
        if qc_audit:
            from .qc import audit_video_quality
            res["qc"] = audit_video_quality(output_path)
        return res

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    profile = job.get("profile") or default_profile

    try:
        if kind == "explainer":
            from .render import produce_explainer
            brief = _load_json_if_path(job.get("input"))
            if not isinstance(brief, dict):
                raise ValueError("explainer job requires a dict or JSON file path in 'input'")
            if profile:
                brief = apply_profile_to_brief(brief, profile)
            ok, info = produce_explainer(brief, output_path)
        elif kind == "project":
            from .render import render_project
            proj = _load_json_if_path(job.get("input"))
            if not isinstance(proj, dict):
                raise ValueError("project job requires a dict or JSON file path in 'input'")
            ok, info = render_project(proj, output_path)
        elif kind == "bundle":
            from .bundle import produce_from_bundle
            bundle = _load_json_if_path(job.get("input"))
            if not isinstance(bundle, dict):
                raise ValueError("bundle job requires a dict or JSON file path in 'input'")
            ok, info = produce_from_bundle(
                bundle,
                output_path,
                aspect=job.get("aspect"),
                profile=profile,
                parallel=bool(job.get("parallel", True)),
                qc_audit=False,
            )
        elif kind == "smart_cut":
            from .smart_cut import smart_cut_media
            media_in = str(job.get("input") or "")
            ok, info = smart_cut_media(
                media_in,
                output_path,
                noise_db=float(job.get("noise_db", -35.0)),
                min_silence=float(job.get("min_silence", 0.55)),
            )
        else:
            raise ValueError(f"Unsupported batch job kind: {kind!r}")

        elapsed = round(time.monotonic() - t0, 3)
        entry: dict[str, Any] = {
            "id": job_id,
            "kind": kind,
            "output": output_path,
            "status": "succeeded" if ok else "failed",
            "elapsed_sec": elapsed,
        }
        if not ok:
            entry["error"] = info
        elif qc_audit:
            from .qc import audit_video_quality
            entry["qc"] = audit_video_quality(output_path)
        return entry
    except Exception as exc:
        return {
            "id": job_id,
            "kind": kind,
            "output": output_path,
            "status": "failed",
            "error": str(exc),
            "elapsed_sec": round(time.monotonic() - t0, 3),
        }


def run_batch_production(
    manifest: dict[str, Any] | list[dict[str, Any]],
    max_concurrency: int | None = None,
    resume: bool = True,
    default_profile: str | None = None,
    qc_audit: bool = False,
    report_path: str | None = None,
) -> dict[str, Any]:
    """Run a batch of video production jobs with error isolation and reporting."""
    if isinstance(manifest, list):
        jobs = manifest
    elif isinstance(manifest, dict):
        jobs = manifest.get("jobs", [])
        if default_profile is None:
            default_profile = manifest.get("default_profile")
        if max_concurrency is None and manifest.get("max_concurrency"):
            max_concurrency = int(manifest["max_concurrency"])
    else:
        raise ValueError("manifest must be a dict with 'jobs' or a list of job dicts")

    hw = detect_hardware_profile()
    workers = max(1, int(max_concurrency or min(2, hw["safe_max_workers"])))
    t0 = time.monotonic()

    results: list[dict[str, Any]] = []
    if workers == 1 or len(jobs) <= 1:
        for j in jobs:
            results.append(_execute_single_job(j, resume, default_profile, qc_audit))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [
                ex.submit(_execute_single_job, j, resume, default_profile, qc_audit)
                for j in jobs
            ]
            for f in futs:
                results.append(f.result())

    succeeded = sum(1 for r in results if r["status"] == "succeeded")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    failed = sum(1 for r in results if r["status"] == "failed")
    summary = {
        "total": len(results),
        "succeeded": succeeded,
        "skipped": skipped,
        "failed": failed,
        "all_ok": failed == 0,
        "max_concurrency": workers,
        "elapsed_sec": round(time.monotonic() - t0, 3),
        "results": results,
    }

    if report_path:
        os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, ensure_ascii=False, default=str)
        summary["report_path"] = report_path

    return summary
