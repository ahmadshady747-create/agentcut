"""AgentCut Multi-Genre Content Templates Engine (v1.50.0).

Provides 8 production-ready, domain-tuned content templates across major video genres:
  1. finance_data_story     - Financial analysis, market trends, crypto, and data storytelling
  2. history_documentary    - Historical narratives, archival storytelling, and timelines
  3. tech_code_explainer    - Software architecture, AI engineering, and code deep-dives
  4. top_list_countdown     - Top-5 / Top-10 countdown listicles with kinetic number badges
  5. news_breaking_brief    - Fast-paced broadcast news, bulletins, and breaking alerts
  6. educational_lesson     - Mini-courses, academic lessons, and concept breakdowns with takeaways
  7. podcast_audiogram_quote - Highlight clips from podcasts, interviews, and quote audiograms
  8. product_promo_showcase - 4-stage SaaS/product launch promotional conversion videos

Strict Zero External Python Dependencies constraint (Python stdlib + FFmpeg only).
"""

from __future__ import annotations

import copy
import os
import uuid
from typing import Any, Dict, List, Optional

from .colors import contrast_ratio, is_readable, ensure_readable


# Standard Aspect Ratio Dimensions (width, height, fps)
ASPECT_PRESETS: Dict[str, Dict[str, int]] = {
    "9:16": {"width": 720, "height": 1280, "fps": 30},
    "16:9": {"width": 1280, "height": 720, "fps": 30},
    "1:1": {"width": 1080, "height": 1080, "fps": 30},
    "4:5": {"width": 1080, "height": 1350, "fps": 30},
}


CONTENT_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "finance_data_story": {
        "id": "finance_data_story",
        "name": "قصة بيانات مالية / Finance Data Story",
        "name_ar": "قصة بيانات مالية",
        "name_en": "Finance Data Story",
        "category": "finance",
        "description": "فيديوهات المال والاستثمار والعملات والبيانات مع رسوم بيانية متحركة وبطاقات مقارنة شفافة وعدادات أرقام حية.",
        "description_en": "Deep-slate financial and market data storytelling with spring-animated line charts, comparison cards, and live currency counters.",
        "default_aspect": "9:16",
        "supported_aspects": ["9:16", "16:9", "1:1", "4:5"],
        "style": "blackboard",
        "look": "cinematic",
        "colors": {
            "base": "#0F172A",
            "accent": "#FACC15",
            "secondary": "#22D3EE",
            "text_primary": "#F8FAFC",
            "text_accent": "#FACC15",
            "card_bg": "#1E293B",
            "border": "#334155",
        },
        "typography": {
            "caption_preset": "karaoke_pill",
            "highlight_color": "yellow",
            "highlight_mode": "word",
            "title_style": "bold_gold",
            "font": "cairo",
        },
        "transitions": ["whip_pan", "crossfade"],
        "motion": "gentle",
        "chapters": True,
        "pacing": True,
        "audio": {
            "ambient": "ambient",
            "duck_amount_db": 10.0,
            "entrance_sfx": "ding",
            "keyword_sfx": "pop",
        },
        "scene_roles": {
            "hook": {"kind": "counter", "suffix": "٪", "sfx": "ding"},
            "problem": {"template": "comparison_cards", "sfx": "pop"},
            "data_proof": {"template": "chart", "sfx": "ding"},
            "comparison": {"template": "balance", "sfx": "whoosh"},
            "process": {"template": "process_flow", "sfx": "pop"},
            "conclusion": {"kind": "counter", "badge": True, "sfx": "ding"},
        },
    },

    "history_documentary": {
        "id": "history_documentary",
        "name": "وثائقي تاريخي وسينمائي / History Documentary",
        "name_ar": "وثائقي تاريخي وسينمائي",
        "name_en": "History Documentary",
        "category": "documentary",
        "description": "سرد قصصي تاريخي سينمائي مع خطوط زمنية، حركة كاميرا هادئة، واقتباسات ذات عمق درامي.",
        "description_en": "Warm cinematic historical storytelling with chronological timelines, slow camera drift, and dignified quote cards.",
        "default_aspect": "16:9",
        "supported_aspects": ["16:9", "9:16", "1:1"],
        "style": "blackboard",
        "look": "warm",
        "colors": {
            "base": "#1C1917",
            "accent": "#D97706",
            "secondary": "#E7E5E4",
            "text_primary": "#F5F5F4",
            "text_accent": "#F59E0B",
            "card_bg": "#292524",
            "border": "#44403C",
        },
        "typography": {
            "caption_preset": "plain",
            "highlight_color": "yellow",
            "highlight_mode": "word",
            "title_style": "quote",
            "font": "cairo",
        },
        "transitions": ["light_leak", "crossfade"],
        "motion": "gentle",
        "chapters": True,
        "pacing": True,
        "audio": {
            "ambient": "ambient",
            "duck_amount_db": 14.0,
            "entrance_sfx": "whoosh",
            "keyword_sfx": "ding",
        },
        "scene_roles": {
            "hook": {"template": "timeline", "sfx": "whoosh"},
            "problem": {"template": "causes", "sfx": "whoosh"},
            "data_proof": {"template": "timeline", "sfx": "ding"},
            "comparison": {"template": "balance", "sfx": "whoosh"},
            "process": {"template": "timeline", "sfx": "ding"},
            "conclusion": {"template": "causes", "sfx": "ding"},
        },
    },

    "tech_code_explainer": {
        "id": "tech_code_explainer",
        "name": "شرح تقني وبرمجي / Tech & Code Explainer",
        "name_ar": "شرح تقني وبرمجي",
        "name_en": "Tech & Code Explainer",
        "category": "tech",
        "description": "فيديوهات التقنية والذكاء الاصطناعي مع مخططات معمارية وتأثيرات غليتش وشارات برمجية حية.",
        "description_en": "Modern developer theme with process flows, neon green/sky accents, glitch transitions, and progress indicators.",
        "default_aspect": "9:16",
        "supported_aspects": ["9:16", "16:9", "1:1"],
        "style": "blackboard",
        "look": "teal_orange",
        "colors": {
            "base": "#090D16",
            "accent": "#10B981",
            "secondary": "#38BDF8",
            "text_primary": "#F8FAFC",
            "text_accent": "#34D399",
            "card_bg": "#0F172A",
            "border": "#1E293B",
        },
        "typography": {
            "caption_preset": "karaoke",
            "highlight_color": "green",
            "highlight_mode": "word",
            "title_style": "neon",
            "font": "segoe-ui",
        },
        "transitions": ["glitch", "whip_pan"],
        "motion": "gentle",
        "chapters": True,
        "pacing": True,
        "audio": {
            "ambient": "ambient",
            "duck_amount_db": 8.0,
            "entrance_sfx": "pop",
            "keyword_sfx": "pop",
        },
        "scene_roles": {
            "hook": {"kind": "badge", "text": "AI Engineering", "sfx": "pop"},
            "problem": {"template": "process_flow", "sfx": "pop"},
            "data_proof": {"template": "chart", "sfx": "ding"},
            "comparison": {"template": "comparison_cards", "sfx": "whoosh"},
            "process": {"template": "process_flow", "sfx": "pop"},
            "conclusion": {"kind": "progress_bar", "sfx": "ding"},
        },
    },

    "top_list_countdown": {
        "id": "top_list_countdown",
        "name": "قائمة عد تنازلي وأفضل 5 / Top-List Countdown",
        "name_ar": "قائمة عد تنازلي وأفضل 5",
        "name_en": "Top-List Countdown",
        "category": "entertainment",
        "description": "فيديوهات القوائم والعد التنازلي التفاعلي مع شارات الترقيم وحركات زنبركية سريعة وانتقالات خاطفة.",
        "description_en": "High-energy countdown listicle with sequential rank badges, spring-animated cards, and zoom transitions.",
        "default_aspect": "9:16",
        "supported_aspects": ["9:16", "1:1", "16:9"],
        "style": "blackboard",
        "look": "cinematic",
        "colors": {
            "base": "#18181B",
            "accent": "#F97316",
            "secondary": "#A855F7",
            "text_primary": "#FFFFFF",
            "text_accent": "#FB923C",
            "card_bg": "#27272A",
            "border": "#3F3F46",
        },
        "typography": {
            "caption_preset": "karaoke_pill",
            "highlight_color": "yellow",
            "highlight_mode": "word",
            "title_style": "pop",
            "font": "cairo",
        },
        "transitions": ["zoom_out", "whip_pan"],
        "motion": "gentle",
        "chapters": True,
        "pacing": True,
        "audio": {
            "ambient": "ambient",
            "duck_amount_db": 8.0,
            "entrance_sfx": "whoosh",
            "keyword_sfx": "pop",
        },
        "scene_roles": {
            "hook": {"kind": "counter", "from": 5, "to": 1, "suffix": "TOP", "sfx": "whoosh"},
            "ranking": {"kind": "badge", "sfx": "pop"},
            "data_proof": {"template": "basket", "sfx": "ding"},
            "comparison": {"template": "comparison_cards", "sfx": "whoosh"},
            "process": {"template": "process_flow", "sfx": "pop"},
            "conclusion": {"kind": "badge", "text": "🏆 #1 Winner", "sfx": "ding"},
        },
    },

    "news_breaking_brief": {
        "id": "news_breaking_brief",
        "name": "موجز إخباري عاجل / Breaking News Brief",
        "name_ar": "موجز إخباري عاجل",
        "name_en": "Breaking News Brief",
        "category": "news",
        "description": "تغطية إخبارية عاجلة مع شريط تقدم سفلي، شارات عاجل، وأشرطة تعريفية سفلية للمصادر.",
        "description_en": "Fast-paced broadcast bulletin with crimson breaking badges, persistent timeline bar, and prominent lower-thirds.",
        "default_aspect": "9:16",
        "supported_aspects": ["9:16", "16:9", "1:1"],
        "style": "blackboard",
        "look": "cool",
        "colors": {
            "base": "#0F172A",
            "accent": "#EF4444",
            "secondary": "#FFFFFF",
            "text_primary": "#FFFFFF",
            "text_accent": "#F87171",
            "card_bg": "#1E293B",
            "border": "#334155",
        },
        "typography": {
            "caption_preset": "plain",
            "highlight_color": "red",
            "highlight_mode": "word",
            "title_style": "breaking",
            "font": "segoe-ui",
        },
        "transitions": ["crossfade", "whip_pan"],
        "motion": "gentle",
        "chapters": False,
        "pacing": True,
        "audio": {
            "ambient": "ambient",
            "duck_amount_db": 10.0,
            "entrance_sfx": "ding",
            "keyword_sfx": "pop",
        },
        "scene_roles": {
            "hook": {"kind": "badge", "text": "🔴 عاجل / BREAKING", "sfx": "ding"},
            "problem": {"template": "causes", "sfx": "pop"},
            "data_proof": {"template": "chart", "sfx": "ding"},
            "comparison": {"template": "balance", "sfx": "whoosh"},
            "process": {"template": "process_flow", "sfx": "pop"},
            "conclusion": {"kind": "progress_bar", "sfx": "ding"},
        },
    },

    "educational_lesson": {
        "id": "educational_lesson",
        "name": "درس تعليمي مصغر / Educational Lesson",
        "name_ar": "درس تعليمي مصغر",
        "name_en": "Educational Lesson",
        "category": "education",
        "description": "دروس تفاعلية مع تفكيك المفاهيم، تسلسل الفصول، وبطاقة تلخيص لأهم النقاط في ختام الدرس.",
        "description_en": "Interactive micro-lesson with structured chapters, concept breakdown diagrams, and automated final takeaway cards.",
        "default_aspect": "16:9",
        "supported_aspects": ["16:9", "9:16", "1:1"],
        "style": "blackboard",
        "look": "cinematic",
        "colors": {
            "base": "#1E293B",
            "accent": "#38BDF8",
            "secondary": "#34D399",
            "text_primary": "#F8FAFC",
            "text_accent": "#7DD3FC",
            "card_bg": "#0F172A",
            "border": "#334155",
        },
        "typography": {
            "caption_preset": "karaoke_pill",
            "highlight_color": "cyan",
            "highlight_mode": "word",
            "title_style": "lesson",
            "font": "cairo",
        },
        "transitions": ["crossfade"],
        "motion": "gentle",
        "chapters": True,
        "pacing": True,
        "audio": {
            "ambient": "ambient",
            "duck_amount_db": 8.0,
            "entrance_sfx": "ding",
            "keyword_sfx": "pop",
        },
        "scene_roles": {
            "hook": {"kind": "badge", "text": "📌 فكرة اليوم", "sfx": "ding"},
            "problem": {"template": "causes", "sfx": "pop"},
            "data_proof": {"template": "chart", "sfx": "ding"},
            "comparison": {"template": "balance", "sfx": "whoosh"},
            "process": {"template": "process_flow", "sfx": "pop"},
            "conclusion": {"template": "comparison_cards", "sfx": "ding"},
        },
    },

    "podcast_audiogram_quote": {
        "id": "podcast_audiogram_quote",
        "name": "اقتباس بودكاست صوتي / Podcast Audiogram",
        "name_ar": "اقتباس بودكاست صوتي",
        "name_en": "Podcast Audiogram",
        "category": "podcast",
        "description": "مقاطع اقتباسات البودكاست مع كاريوكي دقيق في المنتصف، شريط تقدم زمني، وشارة تعريفية للمتحدث.",
        "description_en": "Spotlight quote audiogram with centered active karaoke words, continuous audio progress bar, and speaker badge.",
        "default_aspect": "9:16",
        "supported_aspects": ["9:16", "1:1", "16:9"],
        "style": "blackboard",
        "look": "warm",
        "colors": {
            "base": "#0B0F19",
            "accent": "#F43F5E",
            "secondary": "#FB7185",
            "text_primary": "#FFFFFF",
            "text_accent": "#FDA4AF",
            "card_bg": "#1E293B",
            "border": "#334155",
        },
        "typography": {
            "caption_preset": "karaoke",
            "highlight_color": "magenta",
            "highlight_mode": "word",
            "title_style": "quote",
            "font": "cairo",
        },
        "transitions": ["crossfade"],
        "motion": "gentle",
        "chapters": False,
        "pacing": False,
        "audio": {
            "ambient": "ambient",
            "duck_amount_db": 6.0,
            "entrance_sfx": "whoosh",
            "keyword_sfx": "pop",
        },
        "scene_roles": {
            "hook": {"kind": "badge", "text": "🎙️ بودكاست", "sfx": "whoosh"},
            "problem": {"template": "causes", "sfx": "pop"},
            "data_proof": {"template": "chart", "sfx": "ding"},
            "comparison": {"template": "balance", "sfx": "whoosh"},
            "process": {"template": "process_flow", "sfx": "pop"},
            "conclusion": {"kind": "progress_bar", "sfx": "ding"},
        },
    },

    "product_promo_showcase": {
        "id": "product_promo_showcase",
        "name": "عرض ترويجي لمنتج / Product Promo Showcase",
        "name_ar": "عرض ترويجي لمنتج",
        "name_en": "Product Promo Showcase",
        "category": "marketing",
        "description": "فيديو تسويقي بـ 4 مراحل (المشكلة -> الحل -> إثبات بالأرقام -> دعوة لاتخاذ إجراء CTA).",
        "description_en": "High-converting 4-stage SaaS/product showcase (Hook -> Solution -> Proof Metrics -> CTA Badge).",
        "default_aspect": "9:16",
        "supported_aspects": ["9:16", "16:9", "1:1"],
        "style": "blackboard",
        "look": "teal_orange",
        "colors": {
            "base": "#111827",
            "accent": "#6366F1",
            "secondary": "#EC4899",
            "text_primary": "#F9FAFB",
            "text_accent": "#A5B4FC",
            "card_bg": "#1F2937",
            "border": "#374151",
        },
        "typography": {
            "caption_preset": "karaoke_pill",
            "highlight_color": "cyan",
            "highlight_mode": "word",
            "title_style": "bold",
            "font": "segoe-ui",
        },
        "transitions": ["zoom_out", "whip_pan"],
        "motion": "gentle",
        "chapters": False,
        "pacing": True,
        "audio": {
            "ambient": "ambient",
            "duck_amount_db": 8.0,
            "entrance_sfx": "pop",
            "keyword_sfx": "pop",
        },
        "scene_roles": {
            "hook": {"kind": "badge", "text": "🚀 إطلاق جديد", "sfx": "pop"},
            "problem": {"template": "comparison_cards", "sfx": "pop"},
            "data_proof": {"kind": "counter", "from": 0, "to": 10, "suffix": "X", "sfx": "ding"},
            "comparison": {"template": "balance", "sfx": "whoosh"},
            "process": {"template": "process_flow", "sfx": "pop"},
            "conclusion": {"kind": "badge", "text": "👉 ابدأ مجاناً الآن", "sfx": "ding"},
        },
    },
}


def list_content_templates(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return all content templates, optionally filtered by category.

    Categories: 'finance', 'documentary', 'tech', 'entertainment', 'news', 'education', 'podcast', 'marketing'.
    """
    results = []
    for tid, tpl in CONTENT_TEMPLATES.items():
        if category and category.lower() != "all" and tpl.get("category") != category.lower():
            continue
        entry = copy.deepcopy(tpl)
        # Verify WCAG contrast for template base vs text_primary
        base_hex = entry["colors"]["base"]
        text_hex = entry["colors"]["text_primary"]
        c_ratio = contrast_ratio(text_hex, base_hex)
        entry["colors"]["contrast_ratio"] = c_ratio
        entry["colors"]["wcag_aa"] = is_readable(text_hex, base_hex, 4.5)
        entry["colors"]["wcag_aaa"] = is_readable(text_hex, base_hex, 7.0)
        results.append(entry)
    return results


def get_content_template(template_id: str) -> Dict[str, Any]:
    """Retrieve a single content template by its identifier, or raise ValueError."""
    if template_id not in CONTENT_TEMPLATES:
        known = ", ".join(sorted(CONTENT_TEMPLATES.keys()))
        raise ValueError(f"Unknown content template '{template_id}'. Available templates: {known}")
    return copy.deepcopy(CONTENT_TEMPLATES[template_id])


get_template = get_content_template


def build_brief_from_template(
    template_id: str,
    topic_title: str,
    scenes_input: List[Dict[str, Any]],
    aspect: str = "9:16",
    **overrides: Any,
) -> Dict[str, Any]:
    """Expand minimal high-level user/agent input into a full AgentCut explainer brief.

    Args:
        template_id: One of the 8 registered content genres.
        topic_title: Main on-screen topic title.
        scenes_input: List of scene objects with {title, narration_text, role, data, ...}.
        aspect: Aspect ratio preset ('9:16', '16:9', '1:1', '4:5').
        **overrides: Optional overrides for brief top-level keys.

    Returns:
        A fully validated, turnkey brief dictionary ready for produce_explainer.
    """
    tpl = get_content_template(template_id)
    aspect_cfg = ASPECT_PRESETS.get(aspect, ASPECT_PRESETS["9:16"])
    width = aspect_cfg["width"]
    height = aspect_cfg["height"]

    brief_id = f"{template_id}_{uuid.uuid4().hex[:6]}"
    brief: Dict[str, Any] = {
        "id": brief_id,
        "template": template_id,
        "style": tpl.get("style", "blackboard"),
        "look": tpl.get("look", "cinematic"),
        "output": dict(aspect_cfg),
        "background": "canvas",
        "title": topic_title,
        "chapters": tpl.get("chapters", True),
        "motion": tpl.get("motion", "gentle"),
        "music": "ambient" if tpl.get("audio", {}).get("ambient") else None,
        "duck_amount_db": tpl.get("audio", {}).get("duck_amount_db", 10.0),
        "captions_style": tpl.get("typography", {}).get("caption_preset", "karaoke"),
        "highlight_color": tpl.get("typography", {}).get("highlight_color", "yellow"),
        "highlight_mode": tpl.get("typography", {}).get("highlight_mode", "word"),
        "pacing": tpl.get("pacing", True),
        "scenes": [],
    }

    transitions = tpl.get("transitions", ["crossfade"])
    sfx_profile = tpl.get("audio", {})
    roles_cfg = tpl.get("scene_roles", {})

    total_scenes = len(scenes_input)
    default_role_sequence = ["hook", "problem", "data_proof", "comparison", "process", "conclusion"]

    for si, sc_in in enumerate(scenes_input):
        sid = sc_in.get("id") or f"s{si + 1}"
        stitle = sc_in.get("title") or f"المشهد {si + 1}"

        # Assign role if not specified
        role = sc_in.get("role")
        if not role:
            if si == 0:
                role = "hook"
            elif si == total_scenes - 1:
                role = "conclusion"
            else:
                idx = 1 + ((si - 1) % (len(default_role_sequence) - 2))
                role = default_role_sequence[idx]

        scene_obj: Dict[str, Any] = {
            "id": sid,
            "title": stitle,
        }

        # Transition assignment
        if si > 0:
            scene_obj["transition"] = sc_in.get("transition") or transitions[(si - 1) % len(transitions)]

        # Narration & Captions
        if "narration" in sc_in:
            scene_obj["narration"] = sc_in["narration"]
        if "captions" in sc_in:
            scene_obj["captions"] = sc_in["captions"]
        if "narration_text" in sc_in:
            scene_obj["narration_text"] = sc_in["narration_text"]
        elif "text" in sc_in:
            scene_obj["narration_text"] = sc_in["text"]

        # Diagram assignment
        role_spec = roles_cfg.get(role, {})
        if "diagram" in sc_in:
            scene_obj["diagram"] = sc_in["diagram"]
        elif "template" in role_spec:
            dtpl = role_spec["template"]
            diag_obj: Dict[str, Any] = {
                "template": dtpl,
                "draw": 2.5,
                "size": 0.75 if aspect == "9:16" else 0.55,
                "card": True,
                "sfx": role_spec.get("sfx") or sfx_profile.get("entrance_sfx", "ding"),
            }
            if dtpl == "chart":
                vals = sc_in.get("data")
                if isinstance(vals, (list, tuple)) and 2 <= len(vals) <= 8:
                    diag_obj["params"] = {"values": [float(v) for v in vals]}
                else:
                    diag_obj["params"] = {"values": [20, 35, 60, 95]}
            elif dtpl in ("timeline", "process_flow"):
                lbls = sc_in.get("points") or sc_in.get("labels")
                if isinstance(lbls, (list, tuple)) and len(lbls) >= 2:
                    diag_obj["params"] = {"steps": min(4, len(lbls)), "labels": [str(x) for x in lbls[:4]]}
                else:
                    diag_obj["params"] = {"steps": 3, "labels": ["المرحلة 1", "المرحلة 2", "المرحلة 3"]}
            elif dtpl == "causes":
                diag_obj["params"] = {"rows": 3}
            elif dtpl == "balance":
                diag_obj["params"] = {"left": 70, "right": 30, "left_label": "النمو", "right_label": "المخاطر"}
            elif dtpl == "basket":
                diag_obj["params"] = {"coins": 5}
            scene_obj["diagram"] = diag_obj

        # Keywords / Badges / Counters
        if "keywords" in sc_in:
            scene_obj["keywords"] = sc_in["keywords"]
        elif role_spec.get("kind") == "badge" or role_spec.get("badge"):
            btext = sc_in.get("badge_text") or role_spec.get("text") or stitle
            scene_obj["keywords"] = [{
                "text": btext,
                "start": 1.0,
                "end": 4.5,
                "sfx": role_spec.get("sfx") or sfx_profile.get("keyword_sfx", "pop"),
            }]

        brief["scenes"].append(scene_obj)

    # Apply any user overrides
    for k, v in overrides.items():
        if v is not None:
            brief[k] = v

    return brief


def apply_template_to_brief(brief: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich an existing explainer brief with defaults from its declared 'template'.

    Called automatically when a brief contains `"template": "<template_id>"`.
    """
    template_id = brief.get("template")
    if not template_id or template_id not in CONTENT_TEMPLATES:
        return brief

    tpl = CONTENT_TEMPLATES[template_id]
    enriched = copy.deepcopy(brief)

    # Fill top-level defaults
    if "style" not in enriched:
        enriched["style"] = tpl.get("style", "blackboard")
    if "look" not in enriched:
        enriched["look"] = tpl.get("look", "cinematic")
    if "captions_style" not in enriched:
        enriched["captions_style"] = tpl.get("typography", {}).get("caption_preset", "karaoke")
    if "highlight_color" not in enriched:
        enriched["highlight_color"] = tpl.get("typography", {}).get("highlight_color", "yellow")
    if "highlight_mode" not in enriched:
        enriched["highlight_mode"] = tpl.get("typography", {}).get("highlight_mode", "word")
    if "duck_amount_db" not in enriched:
        enriched["duck_amount_db"] = tpl.get("audio", {}).get("duck_amount_db", 10.0)
    if "pacing" not in enriched:
        enriched["pacing"] = tpl.get("pacing", True)
    if "background" not in enriched:
        enriched["background"] = "canvas"

    return enriched
