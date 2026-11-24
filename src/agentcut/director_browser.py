"""Smart Arabic Script-to-Spec Director for AgentCut (v3.4.5).

Deterministically converts raw Arabic or mixed Arabic/English scripts into a
rich, varied `spec.json` ready for the browser render engine:
- Splits paragraphs or sentences into logical scenes.
- Infers optimal scene types (`intro`, `question`, `statistic`, `comparison`,
  `process`, `layers`, `cycle`, `callout`, `conclusion`) with anti-repetition
  variety guards so consecutive scenes never repeat the same template.
- Matches Arabic & English domain keywords to the 104-icon library (`icons.py`)
  and 6 composite SVG illustrations.
- Extracts numbers/percentages for `badge` and `statistic` cards, and tags
  keywords for kinetic caption highlighting.
"""
from __future__ import annotations

import re
from typing import Any

from .brand_kits import apply_brand_kit

_PARA_SPLIT = re.compile(r"\n\s*\n+")
_SENT_SPLIT = re.compile(r"(?<=[.؟!])\s+")
_NUM_EXTRACT = re.compile(r"([×x]?\s*[0-9\u0660-\u0669]+(?:\.[0-9\u0660-\u0669]+)?\s*[%٪°]?)")

# Arabic/English keyword -> icon mapping across our 104-icon catalog
_KEYWORD_ICONS: list[tuple[tuple[str, ...], str]] = [
    (("حرارة", "برد", "شتاء", "درجة", "37", "سخونة"), "thermometer"),
    (("ثلج", "صقيع", "متجمد"), "snowflake"),
    (("نار", "حرق", "طاقة", "وقود"), "flame"),
    (("دماغ", "مخ", "عقل", "تفكير", "تحت المهاد"), "brain"),
    (("عصب", "إشارة", "نبضة", "مستقبلات"), "neuron"),
    (("قلب", "نبض", "ضخ"), "heart"),
    (("دم", "أوعية", "شرايين", "وريد"), "vessel"),
    (("عضلات", "ارتجاف", "قوة", "حركة"), "muscle"),
    (("رئة", "تنفس", "أكسجين", "هواء"), "lungs"),
    (("خلية", "خلايا", "بيولوجيا"), "cell"),
    (("نوم", "راحة", "ليل"), "sleep_zzz"),
    (("دواء", "علاج", "قرص"), "pill"),
    (("فيروس", "عدوى", "مناعة"), "virus"),
    (("حماية", "دفاع", "إنذار", "أمان"), "shield"),
    (("كود", "برمجة", "دالة", "سكريبت"), "code"),
    (("طرفية", "أمر", "تشغيل", "CLI"), "terminal"),
    (("معالج", "حاسوب", "عتاد", "CPU"), "cpu"),
    (("ذكاء", "نموذج", "وكيل", "AI"), "robot"),
    (("بيانات", "قاعدة", "تخزين"), "database"),
    (("خادم", "سيرفر", "سحابة"), "server"),
    (("شبكة", "إنترنت", "اتصال"), "network"),
    (("مال", "سعر", "تكلفة", "دولار", "ميزانية"), "coin"),
    (("بنك", "استثمار", "اقتصاد"), "bank"),
    (("نمو", "زيادة", "ارتفاع", "صعود"), "trend_up"),
    (("هبوط", "انخفاض", "خسارة"), "trend_down"),
    (("نسبة", "مئوية", "%", "٪"), "percent"),
    (("وقت", "ساعة", "دقيقة", "ثانية", "زمن"), "clock"),
    (("تاريخ", "قديم", "وثيقة"), "scroll"),
    (("فكرة", "سر", "اكتشاف"), "bulb"),
    (("سرعة", "صاروخ", "انطلاق"), "rocket"),
    (("هدف", "دقة", "معيار"), "target"),
    (("مقارنة", "ميزان", "توازن", "مقابل"), "scale"),
    (("تحذير", "خطر", "مشكلة", "خطأ"), "warning"),
    (("نجاح", "خلاصة", "نتيجة", "حل"), "check"),
]

_FALLBACK_ICONS = (
    "bulb", "sparkles", "compass", "target", "layers",
    "rocket", "shield", "cpu", "chart", "star",
)


def _pick_icon(text: str, index: int, used: set[str]) -> str:
    for keywords, icon_name in _KEYWORD_ICONS:
        if any(k in text for k in keywords):
            if icon_name not in used:
                used.add(icon_name)
                return icon_name
            return icon_name
    for cand in _FALLBACK_ICONS:
        if cand not in used:
            used.add(cand)
            return cand
    return _FALLBACK_ICONS[index % len(_FALLBACK_ICONS)]


def _extract_keywords(text: str, max_kw: int = 3) -> list[str]:
    words = [w.strip(".,،؛:!؟\"'()[]«»") for w in text.split()]
    kws: list[str] = []
    # Prioritize numeric tokens and key domain nouns
    for w in words:
        if not w:
            continue
        if any(ch.isdigit() for ch in w) or any(ch in w for ch in "%٪°×"):
            if w not in kws:
                kws.append(w)
    for w in words:
        if len(kws) >= max_kw:
            break
        if len(w) >= 5 and w not in kws:
            kws.append(w)
    return kws[:max_kw]


def _short_title(text: str, max_words: int = 5) -> str:
    first_clause = re.split(r"[.؟!،:—–-]", text.strip())[0].strip()
    tokens = first_clause.split()
    return " ".join(tokens[:max_words]) or text.strip()[:36]


def _split_script_segments(script_text: str, max_scenes: int = 12) -> list[str]:
    cleaned = str(script_text or "").strip()
    if not cleaned:
        return ["مقدمة سريعة عن الموضوع."]
    paras = [p.strip() for p in _PARA_SPLIT.split(cleaned) if p.strip()]
    if len(paras) >= 3:
        return paras[:max_scenes]
    # Single-paragraph script: split by sentence boundaries
    sents = [s.strip() for s in _SENT_SPLIT.split(cleaned) if s.strip()]
    if len(sents) <= 1:
        # Fallback: split by comma/semicolon or every ~9 words
        clauses = [c.strip() for c in re.split(r"[،؛\n]+", cleaned) if c.strip()]
        if len(clauses) >= 2:
            return clauses[:max_scenes]
    return (sents or [cleaned])[:max_scenes]


def script_to_spec(
    script_text: str,
    title: str = "",
    spec_id: str = "auto-reel",
    brand_kit: str = "tab_ezzay",
    format_name: str = "vertical",
    narration_pattern: str = "s{index}.wav",
    target_duration: float | None = None,
) -> dict[str, Any]:
    """Convert raw Arabic text into a complete, multi-template `spec.json`."""
    segments = _split_script_segments(script_text)
    n = len(segments)
    used_icons: set[str] = set()
    scenes: list[dict[str, Any]] = []
    prev_type = ""

    mid_cycle = ("concept", "layers", "process", "cycle", "timeline", "callout")
    mid_cursor = 0

    for i, seg in enumerate(segments):
        sid = f"s{i + 1}"
        nar_file = narration_pattern.format(index=i + 1, id=sid)
        icon = _pick_icon(seg, i, used_icons)
        kws = _extract_keywords(seg)
        num_match = _NUM_EXTRACT.search(seg)
        num_val = num_match.group(1).strip() if num_match else ""
        words_count = len(seg.split())

        if i == 0:
            sc_type = "intro"
        elif i == n - 1 and n >= 3:
            sc_type = "conclusion"
        elif ("؟" in seg and words_count <= 5) or seg.strip().startswith("طب ازاي"):
            sc_type = "question"
        elif num_val and prev_type != "statistic":
            sc_type = "statistic"
        elif any(w in seg for w in ("مقابل", "بينما", "قبل", "بعد", "الفرق بين")) and prev_type != "comparison":
            sc_type = "comparison"
        else:
            sc_type = mid_cycle[mid_cursor % len(mid_cycle)]
            mid_cursor += 1
            if sc_type == prev_type:
                sc_type = mid_cycle[mid_cursor % len(mid_cycle)]
                mid_cursor += 1

        prev_type = sc_type
        sc: dict[str, Any] = {
            "id": sid,
            "type": sc_type,
            "layout": "body",
            "narration": nar_file,
            "text": seg,
            "title": _short_title(seg),
            "icon": icon,
            "keywords": kws,
        }

        if sc_type == "intro":
            sc["layout"] = "zoom"
            if num_val:
                sc["badge"] = num_val
            sc["body"] = _short_title(seg, 4)
        elif sc_type == "statistic":
            sc["layout"] = "scale"
            sc["stat"] = {"value": num_val or "100%", "label": _short_title(seg, 6)}
            sc["bars"] = [
                {"label": "المستوى الطبيعي", "value": 30, "color": "blue"},
                {"label": "وقت الذروة", "value": 95, "color": "accent"},
            ]
        elif sc_type == "comparison":
            clauses = [c.strip() for c in re.split(r"مقابل|بينما|،", seg) if c.strip()]
            p1 = clauses[0] if clauses else "الحالة الأولى"
            p2 = clauses[1] if len(clauses) > 1 else "الحالة الثانية"
            sc["panels"] = [
                {"title": _short_title(p1, 3), "body": p1[:42], "icon": icon},
                {"title": _short_title(p2, 3), "body": p2[:42], "icon": "check"},
            ]
        elif sc_type in ("process", "timeline"):
            parts = [c.strip() for c in re.split(r"[،؛.]", seg) if c.strip()]
            if len(parts) < 2:
                parts = [_short_title(seg, 3), "الاستجابة المباشرة", "النتيجة النهائية"]
            if sc_type == "process":
                sc["steps"] = [{"text": p} for p in parts[:3]]
            else:
                sc["layout"] = "timeline"
                sc["items"] = [{"tag": f"0{j + 1}", "text": p} for j, p in enumerate(parts[:3])]
        elif sc_type == "layers":
            sc["layout"] = "layers"
            parts = [c.strip() for c in re.split(r"[،؛.]", seg) if c.strip()]
            if len(parts) < 2:
                parts = ["الطبقة الخارجية", "المعالجة الداخلية", "الأثر المباشر"]
            sc["layers"] = [
                {"label": f"طبقة {j + 1}", "text": p}
                for j, p in enumerate(parts[:3])
            ]
        elif sc_type == "cycle":
            sc["layout"] = "cycle"
            sc["body"] = num_val or "الدورة"
            sc["nodes"] = ["إشارة", "معالجة", "استجابة", "توازن"]
        elif sc_type == "callout":
            sc["quote"] = seg
            sc["author"] = title or "خلاصة"
        elif sc_type == "conclusion":
            sc["body"] = _short_title(seg, 6)
            sc["subtitle"] = "تابع السلسلة للمزيد"
        else:
            sc["body"] = _short_title(seg, 5)

        scenes.append(sc)

    inferred_title = title.strip() or _short_title(segments[0], 6)
    spec: dict[str, Any] = {
        "id": spec_id,
        "title": inferred_title,
        "brand_kit": brand_kit,
        "format": format_name,
        "scenes": scenes,
    }
    if target_duration is not None:
        spec["target_duration"] = float(target_duration)
    return apply_brand_kit(spec)
