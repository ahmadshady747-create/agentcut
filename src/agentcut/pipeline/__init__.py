"""Modular AgentCut v3.0.0 Compiler Pipeline (`agentcut.pipeline`).

Decouples Timeline IR compilation, `-filter_complex_script` execution,
live soundscape/J-cut audio mixing, and the self-healing cinematic production
loop from the monolithic `render.py` while maintaining 100% backward compatibility.
"""

from .ir import BeatIR, SceneIR, TimelineIR, compile_brief_to_ir
from .filtergraph import write_filter_script, run_ffmpeg_with_script
from .audio_mixer import materialize_soundscape_audio, mix_soundscape_into_video
from .auto_heal import produce_cinematic_auto_heal

__all__ = [
    "BeatIR",
    "SceneIR",
    "TimelineIR",
    "compile_brief_to_ir",
    "write_filter_script",
    "run_ffmpeg_with_script",
    "materialize_soundscape_audio",
    "mix_soundscape_into_video",
    "produce_cinematic_auto_heal",
]
