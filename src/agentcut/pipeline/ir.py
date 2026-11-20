"""Typed Intermediate Representation (IR) for AgentCut v3.0.0 Compiler Pipeline.

Compiles a high-level explainer/editorial brief into a deterministic, inspectable
`TimelineIR` with resolved focal points, exposure matching filters, camera moves,
transitions, chapter cards, and soundscape schedules — testable in milliseconds
without invoking FFmpeg video encoding.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any

from ..text_metrics import estimate_reading_duration
from ..vision import compute_exposure_match_filter, detect_focal_point


@dataclass
class BeatIR:
    index: int
    start: float
    end: float
    broll: str | None = None
    camera_move: str | None = None
    focal_x: float = 0.5
    focal_y: float = 0.5
    exposure_vf: str = ""

    @property
    def duration(self) -> float:
        return round(max(0.0, self.end - self.start), 3)


@dataclass
class SceneIR:
    id: str
    index: int
    start: float
    end: float
    text: str
    intent: str = "narrative"
    energy: float = 0.55
    transition: str = "none"
    transition_duration: float = 0.0
    narration: str | None = None
    captions: str | None = None
    chapter_card: dict[str, Any] | None = None
    highlight_card: dict[str, Any] | None = None
    beats: list[BeatIR] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return round(max(0.0, self.end - self.start), 3)


@dataclass
class TimelineIR:
    title: str
    style: str
    aspect: str
    cinema_look: str | None
    render_profile: str | None
    total_duration: float
    scenes: list[SceneIR]
    soundscape: dict[str, Any] | None = None
    exposure_target_yavg: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compile_brief_to_ir(
    brief: dict[str, Any],
    auto_focal: bool = False,
    match_exposure: bool = False,
) -> TimelineIR:
    """Compile an explainer brief dict into a structured `TimelineIR`.

    When `auto_focal=True`, runs `detect_focal_point()` on every existing local
    beat image. When `match_exposure=True`, runs `compute_exposure_match_filter()`
    across all local beat images so consecutive shots share unified exposure.
    """
    scenes_raw = brief.get("scenes") or []
    all_media: list[str] = []
    if match_exposure:
        for sc in scenes_raw:
            if isinstance(sc.get("broll"), str) and os.path.isfile(sc["broll"]):
                all_media.append(sc["broll"])
            for b in sc.get("beats") or []:
                if isinstance(b.get("broll"), str) and os.path.isfile(b["broll"]):
                    all_media.append(b["broll"])

    exp_map: dict[str, str] = {}
    target_yavg: float | None = None
    if match_exposure and all_media:
        exp_res = compute_exposure_match_filter(list(dict.fromkeys(all_media)))
        target_yavg = exp_res["target_yavg"]
        for sh in exp_res["shots"]:
            exp_map[sh["media_path"]] = sh["vf"]

    focal_cache: dict[str, tuple[float, float]] = {}

    def _focal_for(path: str | None) -> tuple[float, float]:
        if not auto_focal or not path or not os.path.isfile(path):
            return (0.5, 0.5)
        if path not in focal_cache:
            res = detect_focal_point(path)
            focal_cache[path] = (res["focal_x"], res["focal_y"])
        return focal_cache[path]

    compiled_scenes: list[SceneIR] = []
    cursor = 0.0
    global_beat = 0

    for si, sc in enumerate(scenes_raw):
        text = str(sc.get("text") or "")
        dur = float(sc.get("duration") or estimate_reading_duration(text))
        beats_raw = sc.get("beats") or []
        beats_ir: list[BeatIR] = []

        if beats_raw:
            for b in beats_raw:
                b_src = b.get("broll") or sc.get("broll")
                fx, fy = _focal_for(b_src)
                beats_ir.append(BeatIR(
                    index=global_beat,
                    start=round(float(b.get("start", 0.0)), 3),
                    end=round(float(b.get("end", dur)), 3),
                    broll=b_src,
                    camera_move=b.get("camera_move") or sc.get("camera_move"),
                    focal_x=float(b.get("focal_x", fx)),
                    focal_y=float(b.get("focal_y", fy)),
                    exposure_vf=exp_map.get(b_src or "", ""),
                ))
                global_beat += 1
        else:
            b_src = sc.get("broll")
            fx, fy = _focal_for(b_src)
            beats_ir.append(BeatIR(
                index=global_beat,
                start=0.0,
                end=round(dur, 3),
                broll=b_src,
                camera_move=sc.get("camera_move"),
                focal_x=float(sc.get("focal_x", fx)),
                focal_y=float(sc.get("focal_y", fy)),
                exposure_vf=exp_map.get(b_src or "", ""),
            ))
            global_beat += 1

        compiled_scenes.append(SceneIR(
            id=str(sc.get("id") or f"scene_{si + 1}"),
            index=si,
            start=round(cursor, 3),
            end=round(cursor + dur, 3),
            text=text,
            intent=str(sc.get("_narrative_intent") or "narrative"),
            energy=float(sc.get("_energy") or 0.55),
            transition=str(sc.get("transition") or "none"),
            transition_duration=float(sc.get("transition_duration") or 0.0),
            narration=sc.get("narration"),
            captions=sc.get("captions"),
            chapter_card=sc.get("chapter_card"),
            highlight_card=sc.get("highlight_card"),
            beats=beats_ir,
        ))
        cursor += dur

    return TimelineIR(
        title=str(brief.get("title") or "Untitled"),
        style=str(brief.get("style") or "editorial"),
        aspect=str(brief.get("aspect") or "9:16"),
        cinema_look=brief.get("cinema_look"),
        render_profile=brief.get("render_profile"),
        total_duration=round(cursor, 3),
        scenes=compiled_scenes,
        soundscape=brief.get("soundscape"),
        exposure_target_yavg=target_yavg,
    )
