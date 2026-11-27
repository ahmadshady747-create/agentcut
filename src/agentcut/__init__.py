"""AgentCut v3.4.6: complete browser-rendered Arabic & English video engine (FFmpeg strictly for encoding & audio)."""

__version__ = "3.4.6"

from .beat_sync import (
    compute_rms_envelope,
    detect_energy_peaks,
    detect_natural_pauses,
    analyze_audio_beats,
    align_visual_anchors_to_beats,
    build_scene_beat_profile,
)
from .brand_kits import (
    BRAND_KITS,
    list_brand_kits,
    get_brand_kit,
    register_brand_kit,
    apply_brand_kit,
)
from .browser_render import (
    PLAYWRIGHT_AVAILABLE,
    chromium_available,
    frame_times,
    probe_audio_duration,
    mix_audio,
    measure_loudness,
    audit_audio_mix,
    build_loudnorm_filter,
    encode_frames,
    encode_png_stream_pipe,
    get_fast_scratch_dir,
    capture_frames,
    capture_single_frame,
    render_browser_page,
)
from .captions import (
    CAPTION_STYLES,
    is_latin_token,
    balance_caption_chunks,
    render_caption_html,
)
from .charts import (
    build_line_chart_svg,
    build_donut_chart_svg,
    build_grouped_bars_svg,
)
from .director_browser import script_to_spec
from .icons import (
    ICONS,
    ILLUSTRATIONS,
    icon_svg,
    illustration_svg,
    list_icons,
    list_illustrations,
    search_icons,
)
from .flat_reels import (
    PALETTES,
    FORMATS,
    SCENE_TYPES,
    LAYOUTS,
    TRANSITIONS,
    MOTION_PRESETS,
    PHYSICS_MODES,
    CAMERA_MOVES,
    resolve_dimensions,
    validate_reel_spec,
    compute_scene_timings,
    measure_narration_pace,
    build_flat_reels_html,
    build_video_html,
    build_audio_mix,
    render_flat_reels,
    render_video,
    render_flat_reels_frame,
    render_video_frame,
)
from .multi_export import (
    render_thumbnail_html,
    render_thumbnail_png,
    build_multi_format_html_bundle,
    export_multi_format,
)
from .preflight import preflight_audit, doctor_environment
from .scene_patch import (
    compute_scene_fingerprint,
    compute_spec_scene_manifest,
    diff_scene_fingerprints,
    patch_scene,
    generate_hook_ab_variants,
)
from .soundscape import (
    SFX_CATALOG,
    ROOM_TONE_PRESETS,
    MUSIC_BED_PRESETS,
    list_sfx_kinds,
    list_music_presets,
    synthesize_sfx,
    generate_room_tone,
    generate_ambient_music_bed,
    plan_scene_sfx,
    plan_jl_cut_schedule,
)
from .studio_web import (
    build_studio_ui_html,
    handle_studio_api,
    run_studio_server,
)
from .visual_qc import (
    compute_contrast_ratio,
    audit_html_motion_continuity,
    audit_rendered_frames,
    render_contact_sheet,
)
from .script_polisher import clean_spoken_text, generate_srt_for_scene, polish_script_scenes
from .skills_registry import list_agentcut_skills, get_agentcut_skill, export_agentcut_skills
