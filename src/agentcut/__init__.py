"""AgentCut: an agent-first cinematic documentary video engine. No human timeline UI."""

__version__ = "3.2.0"

from .templates import CONTENT_TEMPLATES, list_content_templates, build_brief_from_template
from .profiles import RENDER_PROFILES, list_render_profiles, get_render_profile, detect_hardware_profile
from .qc import audit_video_quality, audit_editorial_craft
from .bundle import auto_split_beats, validate_media_bundle, bundle_to_editorial_brief, produce_from_bundle
from .smart_cut import detect_speech_segments, build_jump_cut_project, smart_cut_media
from .batch import run_batch_production
from .cinematography import CINEMA_LOOKS, list_cinema_looks, get_cinema_look, choose_narrative_camera_move
from .soundscape import SFX_CATALOG, synthesize_sfx, generate_room_tone, plan_jl_cut_audio
from .director import analyze_narrative_beat, humanize_brief, direct_from_script
from .vision import detect_focal_point, measure_shot_exposure, compute_exposure_match_filter
from .parallax import render_chapter_card, render_parallax_clip, build_video_beat_motion_filter
from .pipeline import compile_brief_to_ir, mix_soundscape_into_video, produce_cinematic_auto_heal
from .brief_repair import repair_brief
from .art_director import STYLE_BIBLES, list_style_bibles, generate_visual_prompts
from .script_polisher import clean_spoken_text, generate_srt_for_scene, polish_script_scenes
from .autopilot import autopilot_produce
from .skills_registry import list_agentcut_skills, get_agentcut_skill, export_agentcut_skills
