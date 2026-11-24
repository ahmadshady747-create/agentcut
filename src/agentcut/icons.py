"""Flat-editorial SVG icon library for AgentCut's browser render engine.

66 hand-drawn, palette-aware SVG icons (viewBox 0 0 64 64) with zero
external dependencies or licence restrictions. Icons use CSS variables
(--paper, --card, --ink, --accent, --blue, --muted, --marker, --line)
and deterministic inner motion classes driven exclusively by
window.setTime(t):
  - svg-spin  : continuous smooth rotation around (32, 32)
  - svg-pulse : organic breathing scale around (32, 32)
  - svg-wave  : horizontal wave oscillation
  - svg-rise  : gentle vertical float / rise
  - svg-tick  : pendulum / metronome rock around (32, 32)
"""
from __future__ import annotations

ICONS: dict[str, str] = {
    # --- Core, Science & Nature (18) ---
    "thermometer": (
        '<circle cx="32" cy="48" r="13" fill="var(--line)" opacity="0.65"/>'
        '<rect x="26" y="4" width="12" height="38" rx="6" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="32" cy="48" r="11" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<g class="svg-rise">'
        '<rect x="29.5" y="12" width="5" height="28" rx="2.5" fill="var(--accent)"/>'
        '</g>'
        '<circle cx="32" cy="48" r="6.5" fill="var(--accent)" class="svg-pulse"/>'
        '<g stroke="var(--ink)" stroke-width="2" stroke-linecap="round">'
        '<line x1="41" y1="14" x2="46" y2="14"/>'
        '<line x1="41" y1="22" x2="48" y2="22"/>'
        '<line x1="41" y1="30" x2="46" y2="30"/></g>'),
    "snowflake": (
        '<circle cx="32" cy="32" r="24" fill="var(--blue)" opacity="0.12"/>'
        '<g class="svg-spin" stroke="var(--blue)" stroke-width="3.6" '
        'stroke-linecap="round" fill="none">'
        '<line x1="32" y1="6" x2="32" y2="58"/>'
        '<line x1="9.5" y1="19" x2="54.5" y2="45"/>'
        '<line x1="54.5" y1="19" x2="9.5" y2="45"/>'
        '<polyline points="27,11 32,16 37,11"/>'
        '<polyline points="27,53 32,48 37,53"/>'
        '<polyline points="12,25 18,24 16,18"/>'
        '<polyline points="52,39 46,40 48,46"/>'
        '<polyline points="52,25 46,24 48,18"/>'
        '<polyline points="12,39 18,40 16,46"/>'
        '</g>'
        '<circle cx="32" cy="32" r="6" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="2.5"/>'
        '<circle cx="32" cy="32" r="2.5" fill="var(--blue)"/>'),
    "flame": (
        '<circle cx="32" cy="38" r="21" fill="var(--marker)" opacity="0.18"/>'
        '<g class="svg-pulse">'
        '<path d="M32 4 C41 16 51 23 51 37 A19 19 0 1 1 13 37 '
        'C13 28 19 23 23 17 C25 23 27 25 31 25 C29 17 30 10 32 4 Z" '
        'fill="var(--accent)" stroke="var(--ink)" stroke-width="2.6" '
        'stroke-linejoin="round"/>'
        '<path d="M32 30 C37 36 41 40 41 46 A9 9 0 1 1 23 46 '
        'C23 40 27 36 32 30 Z" fill="var(--marker)"/>'
        '</g>'),
    "droplet": (
        '<g class="svg-rise">'
        '<path d="M32 6 C41 20 50 28 50 40 A18 18 0 1 1 14 40 '
        'C14 28 23 20 32 6 Z" fill="var(--blue)" '
        'stroke="var(--ink)" stroke-width="2.8" stroke-linejoin="round"/>'
        '<path d="M23 39 A9 9 0 0 0 30 48" stroke="var(--card)" '
        'stroke-width="3" fill="none" stroke-linecap="round"/></g>'),
    "wind": (
        '<g class="svg-wave" stroke="var(--blue)" stroke-width="4.5" '
        'fill="none" stroke-linecap="round">'
        '<path d="M8 19 H42 A7 7 0 1 0 38 7"/>'
        '<path d="M6 32 H54" stroke="var(--ink)"/>'
        '<path d="M10 45 H38 A6 6 0 1 1 34 55"/></g>'),
    "sun": (
        '<g class="svg-spin" stroke="var(--accent)" stroke-width="3.8" '
        'stroke-linecap="round">'
        '<line x1="32" y1="5" x2="32" y2="12"/>'
        '<line x1="32" y1="52" x2="32" y2="59"/>'
        '<line x1="5" y1="32" x2="12" y2="32"/>'
        '<line x1="52" y1="32" x2="59" y2="32"/>'
        '<line x1="13" y1="13" x2="18" y2="18"/>'
        '<line x1="46" y1="46" x2="51" y2="51"/>'
        '<line x1="51" y1="13" x2="46" y2="18"/>'
        '<line x1="18" y1="46" x2="13" y2="51"/></g>'
        '<circle cx="32" cy="32" r="14" fill="var(--marker)" '
        'stroke="var(--ink)" stroke-width="3" class="svg-pulse"/>'),
    "moon": (
        '<g class="svg-rise">'
        '<path d="M40 10 A22 22 0 1 0 54 44 A17 17 0 1 1 40 10 Z" '
        'fill="var(--marker)" stroke="var(--ink)" stroke-width="3" '
        'stroke-linejoin="round"/></g>'
        '<circle cx="48" cy="18" r="3" fill="var(--blue)" class="svg-pulse"/>'
        '<circle cx="55" cy="26" r="2" fill="var(--accent)"/>'),
    "wave": (
        '<g class="svg-wave" fill="none" stroke-width="4.2" stroke-linecap="round">'
        '<path d="M4 32 Q11 14 18 32 T32 32 T46 32 T60 32" stroke="var(--accent)"/>'
        '<path d="M4 42 Q11 28 18 42 T32 42 T46 42 T60 42" stroke="var(--blue)" '
        'opacity="0.75"/></g>'),
    "magnet": (
        '<g class="svg-rise">'
        '<path d="M14 14 V34 A18 18 0 0 0 50 34 V14 H39 V34 '
        'A7 7 0 0 1 25 34 V14 Z" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.8" stroke-linejoin="round"/>'
        '<rect x="14" y="14" width="11" height="8" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="2.5"/>'
        '<rect x="39" y="14" width="11" height="8" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="2.5"/></g>'),
    "atom": (
        '<g class="svg-spin" fill="none" stroke="var(--blue)" stroke-width="2.8">'
        '<ellipse cx="32" cy="32" rx="25" ry="10"/>'
        '<ellipse cx="32" cy="32" rx="25" ry="10" transform="rotate(60 32 32)"/>'
        '<ellipse cx="32" cy="32" rx="25" ry="10" transform="rotate(120 32 32)"/>'
        '</g>'
        '<circle cx="32" cy="32" r="6" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.5" class="svg-pulse"/>'),
    "dna": (
        '<g class="svg-wave" stroke-width="3.2" fill="none" stroke-linecap="round">'
        '<path d="M18 8 C46 18 18 32 46 42 C32 48 22 52 18 56" stroke="var(--accent)"/>'
        '<path d="M46 8 C18 18 46 32 18 42 C32 48 42 52 46 56" stroke="var(--blue)"/>'
        '<line x1="23" y1="16" x2="41" y2="16" stroke="var(--ink)"/>'
        '<line x1="25" y1="32" x2="39" y2="32" stroke="var(--ink)"/>'
        '<line x1="23" y1="48" x2="41" y2="48" stroke="var(--ink)"/></g>'),
    "flask": (
        '<path d="M24 8 H40 V22 L53 48 C55 52 52 56 48 56 H16 '
        'C12 56 9 52 11 48 L24 22 Z" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<path d="M16 40 H48 L52 49 C53 52 51 54 48 54 H16 '
        'C13 54 11 52 12 49 Z" fill="var(--blue)"/>'
        '<g class="svg-rise">'
        '<circle cx="28" cy="46" r="3" fill="var(--marker)"/>'
        '<circle cx="37" cy="43" r="2.5" fill="var(--card)"/></g>'
        '<line x1="20" y1="8" x2="44" y2="8" stroke="var(--ink)" '
        'stroke-width="3.2" stroke-linecap="round"/>'),
    "microscope": (
        '<line x1="14" y1="56" x2="50" y2="56" stroke="var(--ink)" '
        'stroke-width="3.5" stroke-linecap="round"/>'
        '<path d="M36 56 C46 52 50 42 46 32" fill="none" stroke="var(--ink)" '
        'stroke-width="3.5" stroke-linecap="round"/>'
        '<g class="svg-tick">'
        '<rect x="24" y="10" width="12" height="26" rx="3" '
        'transform="rotate(24 30 23)" fill="var(--blue)" '
        'stroke="var(--ink)" stroke-width="2.8"/></g>'
        '<line x1="16" y1="42" x2="38" y2="42" stroke="var(--accent)" '
        'stroke-width="4" stroke-linecap="round"/>'),
    "cloud": (
        '<g class="svg-wave">'
        '<path d="M18 46 H48 A11 11 0 0 0 49 24 A15 15 0 0 0 21 20 '
        'A12 12 0 0 0 18 46 Z" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/></g>'),
    "rain": (
        '<path d="M16 36 H48 A10 10 0 0 0 49 17 A13 13 0 0 0 24 14 '
        'A10 10 0 0 0 16 36 Z" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<g class="svg-rise" stroke="var(--blue)" stroke-width="3.5" '
        'stroke-linecap="round">'
        '<line x1="22" y1="43" x2="19" y2="53"/>'
        '<line x1="33" y1="43" x2="30" y2="53"/>'
        '<line x1="44" y1="43" x2="41" y2="53"/></g>'),
    "fire": (
        '<g class="svg-pulse">'
        '<path d="M32 6 C44 20 52 29 52 40 A20 20 0 1 1 12 40 '
        'C12 31 18 24 24 18 C24 26 28 28 32 26 C30 18 30 12 32 6 Z" '
        'fill="var(--accent)" stroke="var(--ink)" stroke-width="2.8"/>'
        '<circle cx="32" cy="44" r="8" fill="var(--marker)"/></g>'),
    "leaf": (
        '<g class="svg-tick">'
        '<path d="M12 52 C12 24 30 10 54 10 C54 34 40 52 12 52 Z" '
        'fill="var(--blue)" stroke="var(--ink)" stroke-width="3" '
        'stroke-linejoin="round"/>'
        '<path d="M12 52 Q30 34 48 16" fill="none" stroke="var(--card)" '
        'stroke-width="3" stroke-linecap="round"/></g>'),
    "globe": (
        '<circle cx="32" cy="32" r="24" fill="var(--blue)" opacity="0.18"/>'
        '<g class="svg-spin" fill="none" stroke="var(--ink)" stroke-width="2.8">'
        '<circle cx="32" cy="32" r="23"/>'
        '<ellipse cx="32" cy="32" rx="11" ry="23"/>'
        '<line x1="9" y1="32" x2="55" y2="32"/>'
        '<path d="M13 20 Q32 25 51 20"/>'
        '<path d="M13 44 Q32 39 51 44"/></g>'),

    # --- Body & Biology (10) ---
    "body": (
        '<circle cx="32" cy="14" r="9.5" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<path d="M14 56 C14 39 22 31 32 31 C42 31 50 39 50 56 Z" '
        'fill="var(--card)" stroke="var(--ink)" stroke-width="3" '
        'stroke-linejoin="round"/>'
        '<circle cx="32" cy="44" r="7" fill="var(--accent)" class="svg-pulse"/>'
        '<circle cx="32" cy="44" r="11" fill="none" stroke="var(--blue)" '
        'stroke-width="2" stroke-dasharray="4 3" class="svg-spin"/>'),
    "brain": (
        '<g class="svg-pulse">'
        '<path d="M31 12 C20 12 13 19 13 28 C10 31 10 38 14 41 '
        'C14 48 20 53 28 53 L31 53 Z" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.8" stroke-linejoin="round"/>'
        '<path d="M33 12 C44 12 51 19 51 28 C54 31 54 38 50 41 '
        'C50 48 44 53 36 53 L33 53 Z" fill="var(--blue)" '
        'stroke="var(--ink)" stroke-width="2.8" stroke-linejoin="round"/>'
        '<path d="M22 24 Q26 28 22 33 M42 24 Q38 28 42 33" '
        'stroke="var(--paper)" stroke-width="2.6" fill="none" '
        'stroke-linecap="round"/></g>'),
    "heart": (
        '<circle cx="32" cy="34" r="23" fill="var(--accent)" opacity="0.14"/>'
        '<g class="svg-pulse">'
        '<path d="M32 54 C14 42 8 32 8 22 A12 12 0 0 1 32 18 '
        'A12 12 0 0 1 56 22 C56 32 50 42 32 54 Z" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<polyline points="17,32 25,32 29,24 34,40 38,32 47,32" '
        'fill="none" stroke="var(--paper)" stroke-width="3" '
        'stroke-linecap="round" stroke-linejoin="round"/></g>'),
    "lungs": (
        '<line x1="32" y1="8" x2="32" y2="28" stroke="var(--ink)" '
        'stroke-width="3.5" stroke-linecap="round"/>'
        '<g class="svg-pulse">'
        '<path d="M28 24 C16 24 8 34 8 47 C8 53 13 55 19 53 '
        'C24 51 28 46 28 39 Z" fill="var(--blue)" stroke="var(--ink)" '
        'stroke-width="2.8" stroke-linejoin="round"/>'
        '<path d="M36 24 C48 24 56 34 56 47 C56 53 51 55 45 53 '
        'C40 51 36 46 36 39 Z" fill="var(--blue)" stroke="var(--ink)" '
        'stroke-width="2.8" stroke-linejoin="round"/></g>'),
    "hand": (
        '<g class="svg-pulse">'
        '<rect x="14" y="26" width="36" height="24" rx="10" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<rect x="17" y="11" width="6.5" height="19" rx="3.2" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="2.6"/>'
        '<rect x="26" y="8" width="6.5" height="22" rx="3.2" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="2.6"/>'
        '<rect x="35" y="10" width="6.5" height="20" rx="3.2" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="2.6"/>'
        '<rect x="44" y="15" width="6.5" height="15" rx="3.2" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="2.6"/>'
        '<rect x="14" y="43" width="36" height="9" rx="4.5" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.6"/></g>'),
    "eye": (
        '<path d="M5 32 C13 18 22 14 32 14 C42 14 51 18 59 32 '
        'C51 46 42 50 32 50 C22 50 13 46 5 32 Z" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<circle cx="32" cy="32" r="11" fill="var(--blue)" '
        'stroke="var(--ink)" stroke-width="2.6" class="svg-pulse"/>'
        '<circle cx="32" cy="32" r="4.5" fill="var(--ink)"/>'
        '<circle cx="35.5" cy="28.5" r="2" fill="var(--card)"/>'),
    "vessel": (
        '<rect x="4" y="16" width="56" height="32" rx="16" '
        'fill="var(--blue)" opacity="0.14"/>'
        '<g class="svg-wave">'
        '<path d="M6 21 Q20 11 32 21 T58 21" stroke="var(--blue)" '
        'stroke-width="4.5" fill="none" stroke-linecap="round"/>'
        '<path d="M6 43 Q20 33 32 43 T58 43" stroke="var(--blue)" '
        'stroke-width="4.5" fill="none" stroke-linecap="round"/>'
        '<circle cx="20" cy="32" r="4.5" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2"/>'
        '<circle cx="36" cy="32" r="5.5" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2"/>'
        '<circle cx="49" cy="32" r="3.5" fill="var(--marker)" '
        'stroke="var(--ink)" stroke-width="1.8"/></g>'),
    "muscle": (
        '<g class="svg-pulse">'
        '<path d="M12 46 C12 28 22 24 30 28 C32 16 44 12 52 20 '
        'C56 26 52 38 42 44 C34 50 20 52 12 46 Z" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<path d="M24 38 Q34 30 44 34" fill="none" stroke="var(--card)" '
        'stroke-width="3" stroke-linecap="round"/></g>'),
    "cell": (
        '<circle cx="32" cy="32" r="24" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="32" cy="32" r="10" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.5" class="svg-pulse"/>'
        '<g class="svg-spin">'
        '<circle cx="18" cy="22" r="3.5" fill="var(--blue)"/>'
        '<circle cx="46" cy="24" r="3" fill="var(--marker)"/>'
        '<circle cx="44" cy="44" r="3.5" fill="var(--blue)"/>'
        '<circle cx="20" cy="44" r="2.8" fill="var(--accent)"/></g>'),
    "pill": (
        '<g class="svg-tick">'
        '<rect x="10" y="22" width="44" height="20" rx="10" '
        'transform="rotate(-35 32 32)" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<path d="M32 32 L46 22 A10 10 0 0 1 51 35 L38 44 Z" '
        'fill="var(--accent)"/></g>'),

    # --- Tech, Code & AI (14) ---
    "cpu": (
        '<g stroke="var(--ink)" stroke-width="3" stroke-linecap="round">'
        '<line x1="22" y1="6" x2="22" y2="14"/><line x1="32" y1="6" x2="32" y2="14"/>'
        '<line x1="42" y1="6" x2="42" y2="14"/><line x1="22" y1="50" x2="22" y2="58"/>'
        '<line x1="32" y1="50" x2="32" y2="58"/><line x1="42" y1="50" x2="42" y2="58"/>'
        '<line x1="6" y1="22" x2="14" y2="22"/><line x1="6" y1="32" x2="14" y2="32"/>'
        '<line x1="6" y1="42" x2="14" y2="42"/><line x1="50" y1="22" x2="58" y2="22"/>'
        '<line x1="50" y1="32" x2="58" y2="32"/><line x1="50" y1="42" x2="58" y2="42"/></g>'
        '<rect x="14" y="14" width="36" height="36" rx="6" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<rect x="23" y="23" width="18" height="18" rx="3" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.4" class="svg-pulse"/>'),
    "gear": (
        '<g class="svg-spin">'
        '<circle cx="32" cy="32" r="18" fill="none" stroke="var(--ink)" '
        'stroke-width="8" stroke-dasharray="7 5.5"/>'
        '<circle cx="32" cy="32" r="14" fill="var(--blue)" '
        'stroke="var(--ink)" stroke-width="2.8"/></g>'
        '<circle cx="32" cy="32" r="6" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="2.8"/>'),
    "bolt": (
        '<circle cx="32" cy="32" r="24" fill="var(--marker)" opacity="0.22"/>'
        '<g class="svg-pulse">'
        '<polygon points="36,4 14,35 28,35 25,60 50,26 35,26" '
        'fill="var(--marker)" stroke="var(--ink)" stroke-width="3" '
        'stroke-linejoin="round"/></g>'),
    "bulb": (
        '<circle cx="32" cy="25" r="20" fill="var(--marker)" opacity="0.22"/>'
        '<g class="svg-pulse">'
        '<path d="M22 39 C17 34 15 29 15 24 A17 17 0 1 1 49 24 '
        'C49 29 47 34 42 39 L40 45 H24 Z" fill="var(--marker)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/></g>'
        '<rect x="24" y="46" width="16" height="5" rx="2" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="2.5"/>'
        '<rect x="27" y="52" width="10" height="4" rx="2" fill="var(--ink)"/>'),
    "layers": (
        '<g class="svg-rise">'
        '<polygon points="32,7 58,20 32,33 6,20" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.6" stroke-linejoin="round"/></g>'
        '<polygon points="6,31 32,44 58,31 58,38 32,51 6,38" '
        'fill="var(--blue)" stroke="var(--ink)" stroke-width="2.4" '
        'stroke-linejoin="round"/>'
        '<polygon points="10,45 32,56 54,45 54,50 32,60 10,50" '
        'fill="var(--marker)" stroke="var(--ink)" stroke-width="2"/>'),
    "chart": (
        '<line x1="8" y1="55" x2="56" y2="55" stroke="var(--ink)" '
        'stroke-width="3" stroke-linecap="round"/>'
        '<rect x="11" y="35" width="11" height="20" rx="2.5" '
        'fill="var(--blue)" stroke="var(--ink)" stroke-width="2.4"/>'
        '<rect x="26.5" y="24" width="11" height="31" rx="2.5" '
        'fill="var(--marker)" stroke="var(--ink)" stroke-width="2.4"/>'
        '<rect x="42" y="12" width="11" height="43" rx="2.5" '
        'fill="var(--accent)" stroke="var(--ink)" stroke-width="2.4" '
        'class="svg-rise"/>'),
    "code": (
        '<rect x="6" y="12" width="52" height="40" rx="8" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<g class="svg-pulse" fill="none" stroke-width="3.6" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="23,24 15,32 23,40" stroke="var(--accent)"/>'
        '<polyline points="41,24 49,32 41,40" stroke="var(--blue)"/>'
        '<line x1="35" y1="21" x2="29" y2="43" stroke="var(--ink)"/></g>'),
    "terminal": (
        '<rect x="6" y="10" width="52" height="44" rx="8" fill="var(--ink)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="15" cy="18" r="2.5" fill="var(--accent)"/>'
        '<circle cx="23" cy="18" r="2.5" fill="var(--marker)"/>'
        '<circle cx="31" cy="18" r="2.5" fill="var(--blue)"/>'
        '<polyline points="16,31 24,37 16,43" fill="none" stroke="var(--marker)" '
        'stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>'
        '<line x1="29" y1="43" x2="44" y2="43" stroke="var(--paper)" '
        'stroke-width="3.5" stroke-linecap="round" class="svg-pulse"/>'),
    "database": (
        '<ellipse cx="32" cy="15" rx="20" ry="7" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.8"/>'
        '<path d="M12 15 V49 A20 7 0 0 0 52 49 V15" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="2.8"/>'
        '<path d="M12 27 A20 7 0 0 0 52 27" fill="none" stroke="var(--blue)" '
        'stroke-width="2.8"/>'
        '<path d="M12 39 A20 7 0 0 0 52 39" fill="none" stroke="var(--ink)" '
        'stroke-width="2.5"/>'),
    "robot": (
        '<line x1="32" y1="7" x2="32" y2="16" stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="32" cy="7" r="3.5" fill="var(--accent)" class="svg-pulse"/>'
        '<rect x="12" y="16" width="40" height="34" rx="10" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="24" cy="31" r="5" fill="var(--blue)" class="svg-pulse"/>'
        '<circle cx="40" cy="31" r="5" fill="var(--blue)" class="svg-pulse"/>'
        '<rect x="22" y="41" width="20" height="4" rx="2" fill="var(--accent)"/>'),
    "wifi": (
        '<g class="svg-pulse" fill="none" stroke="var(--blue)" '
        'stroke-width="4" stroke-linecap="round">'
        '<path d="M10 25 A32 32 0 0 1 54 25"/>'
        '<path d="M18 35 A20 20 0 0 1 46 35" stroke="var(--ink)"/>'
        '<path d="M25 44 A10 10 0 0 1 39 44" stroke="var(--accent)"/></g>'
        '<circle cx="32" cy="52" r="4" fill="var(--ink)"/>'),
    "server": (
        '<rect x="10" y="10" width="44" height="14" rx="4" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="2.8"/>'
        '<rect x="10" y="26" width="44" height="14" rx="4" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="2.8"/>'
        '<rect x="10" y="42" width="44" height="14" rx="4" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="2.8"/>'
        '<g class="svg-pulse">'
        '<circle cx="18" cy="17" r="2.8" fill="var(--accent)"/>'
        '<circle cx="18" cy="33" r="2.8" fill="var(--blue)"/>'
        '<circle cx="18" cy="49" r="2.8" fill="var(--marker)"/></g>'),
    "chip": (
        '<rect x="16" y="16" width="32" height="32" rx="6" fill="var(--blue)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<rect x="24" y="24" width="16" height="16" rx="3" fill="var(--marker)" '
        'stroke="var(--ink)" stroke-width="2.5" class="svg-pulse"/>'),
    "spark_ai": (
        '<g class="svg-pulse">'
        '<path d="M28 8 L33 25 L50 30 L33 35 L28 52 L23 35 L6 30 L23 25 Z" '
        'fill="var(--accent)" stroke="var(--ink)" stroke-width="2.8" '
        'stroke-linejoin="round"/></g>'
        '<g class="svg-spin">'
        '<path d="M50 10 L52 16 L58 18 L52 20 L50 26 L48 20 L42 18 L48 16 Z" '
        'fill="var(--marker)" stroke="var(--ink)" stroke-width="1.8"/></g>'),

    # --- Editorial, Structure & UI (14) ---
    "factory": (
        '<g class="svg-rise">'
        '<circle cx="50" cy="10" r="4.5" fill="var(--muted)" opacity="0.75"/>'
        '<circle cx="56" cy="5" r="3" fill="var(--muted)" opacity="0.5"/></g>'
        '<rect x="45" y="13" width="9" height="19" rx="1.5" fill="var(--ink)"/>'
        '<path d="M6 56 L6 30 L19 21 L31 30 L43 21 L56 30 L56 56 Z" '
        'fill="var(--card)" stroke="var(--ink)" stroke-width="3" '
        'stroke-linejoin="round"/>'
        '<rect x="13" y="38" width="9" height="10" rx="2" fill="var(--accent)"/>'
        '<rect x="27" y="38" width="9" height="10" rx="2" fill="var(--blue)"/>'
        '<rect x="41" y="38" width="9" height="10" rx="2" fill="var(--marker)"/>'
        '<line x1="4" y1="56" x2="60" y2="56" stroke="var(--ink)" '
        'stroke-width="3" stroke-linecap="round"/>'),
    "door": (
        '<rect x="18" y="6" width="28" height="52" rx="4" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<rect x="23" y="11" width="18" height="19" rx="2.5" '
        'fill="var(--accent)" stroke="var(--ink)" stroke-width="2.2"/>'
        '<circle cx="40" cy="38" r="3.5" fill="var(--marker)" '
        'stroke="var(--ink)" stroke-width="2" class="svg-pulse"/>'
        '<line x1="12" y1="58" x2="52" y2="58" stroke="var(--ink)" '
        'stroke-width="3" stroke-linecap="round"/>'),
    "zigzag": (
        '<g class="svg-wave" stroke-width="4.8" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="8,16 20,8 32,16 44,8 56,16" stroke="var(--accent)"/>'
        '<polyline points="8,32 20,24 32,32 44,24 56,32" stroke="var(--ink)"/>'
        '<polyline points="8,48 20,40 32,48 44,40 56,48" stroke="var(--blue)"/></g>'),
    "check": (
        '<circle cx="32" cy="32" r="27" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="32" cy="32" r="21" fill="none" stroke="var(--paper)" '
        'stroke-width="2" stroke-dasharray="6 5" class="svg-spin"/>'
        '<path d="M20 33 L29 42 L45 23" stroke="var(--paper)" '
        'stroke-width="5.5" fill="none" stroke-linecap="round" '
        'stroke-linejoin="round" class="svg-pulse"/>'),
    "clock": (
        '<circle cx="32" cy="32" r="25" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="32" cy="32" r="20" fill="none" stroke="var(--line)" '
        'stroke-width="2"/>'
        '<line x1="32" y1="32" x2="32" y2="17" stroke="var(--ink)" '
        'stroke-width="3.5" stroke-linecap="round"/>'
        '<g class="svg-spin">'
        '<line x1="32" y1="32" x2="45" y2="32" stroke="var(--accent)" '
        'stroke-width="3" stroke-linecap="round"/></g>'
        '<circle cx="32" cy="32" r="3.5" fill="var(--ink)"/>'),
    "shield": (
        '<g class="svg-pulse">'
        '<path d="M32 6 L52 14 V31 C52 44 43 53 32 58 C21 53 12 44 12 31 '
        'V14 Z" fill="var(--blue)" stroke="var(--ink)" stroke-width="3" '
        'stroke-linejoin="round"/>'
        '<path d="M23 32 L29 38 L42 25" fill="none" stroke="var(--paper)" '
        'stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/></g>'),
    "scale": (
        '<line x1="32" y1="10" x2="32" y2="54" stroke="var(--ink)" '
        'stroke-width="3.2" stroke-linecap="round"/>'
        '<line x1="18" y1="54" x2="46" y2="54" stroke="var(--ink)" '
        'stroke-width="3.5" stroke-linecap="round"/>'
        '<g class="svg-tick">'
        '<line x1="12" y1="20" x2="52" y2="20" stroke="var(--ink)" '
        'stroke-width="3.2" stroke-linecap="round"/>'
        '<path d="M7 34 L12 20 L17 34 Z" fill="var(--blue)" '
        'stroke="var(--ink)" stroke-width="2.2" stroke-linejoin="round"/>'
        '<path d="M47 34 L52 20 L57 34 Z" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.2" stroke-linejoin="round"/></g>'),
    "sparkles": (
        '<g class="svg-pulse">'
        '<path d="M32 6 L37 25 L56 30 L37 35 L32 54 L27 35 L8 30 L27 25 Z" '
        'fill="var(--marker)" stroke="var(--ink)" stroke-width="2.8" '
        'stroke-linejoin="round"/></g>'
        '<circle cx="51" cy="13" r="4" fill="var(--accent)" class="svg-rise"/>'
        '<circle cx="13" cy="49" r="3.5" fill="var(--blue)" class="svg-rise"/>'),
    "arrow_up": (
        '<g class="svg-rise">'
        '<path d="M32 8 L52 28 H40 V56 H24 V28 H12 Z" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/></g>'),
    "arrow_down": (
        '<g class="svg-rise">'
        '<path d="M32 56 L52 36 H40 V8 H24 V36 H12 Z" fill="var(--blue)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/></g>'),
    "target": (
        '<circle cx="32" cy="32" r="24" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="32" cy="32" r="16" fill="var(--blue)" '
        'stroke="var(--ink)" stroke-width="2.6"/>'
        '<circle cx="32" cy="32" r="8" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.4" class="svg-pulse"/>'),
    "warning": (
        '<g class="svg-pulse">'
        '<polygon points="32,8 57,54 7,54" fill="var(--marker)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<line x1="32" y1="24" x2="32" y2="39" stroke="var(--ink)" '
        'stroke-width="4" stroke-linecap="round"/>'
        '<circle cx="32" cy="47" r="2.8" fill="var(--ink)"/></g>'),
    "star": (
        '<g class="svg-pulse">'
        '<polygon points="32,6 39,23 58,25 43,37 48,55 32,45 16,55 21,37 6,25 25,23" '
        'fill="var(--marker)" stroke="var(--ink)" stroke-width="2.8" '
        'stroke-linejoin="round"/></g>'),
    "trophy": (
        '<g class="svg-rise">'
        '<path d="M18 10 H46 V24 C46 33 40 40 32 40 C24 40 18 33 18 24 Z" '
        'fill="var(--marker)" stroke="var(--ink)" stroke-width="3"/>'
        '<path d="M18 15 H10 C10 24 13 28 18 28 M46 15 H54 C54 24 51 28 46 28" '
        'fill="none" stroke="var(--ink)" stroke-width="2.8"/>'
        '<line x1="32" y1="40" x2="32" y2="52" stroke="var(--ink)" stroke-width="3.5"/>'
        '<rect x="20" y="51" width="24" height="6" rx="2" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.5"/></g>'),

    # --- Storytelling, Finance & Media (10) ---
    "mic": (
        '<rect x="23" y="8" width="18" height="28" rx="9" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="3" class="svg-pulse"/>'
        '<path d="M15 30 A17 17 0 0 0 49 30" fill="none" stroke="var(--ink)" '
        'stroke-width="3.2" stroke-linecap="round"/>'
        '<line x1="32" y1="47" x2="32" y2="56" stroke="var(--ink)" stroke-width="3.2"/>'
        '<line x1="22" y1="56" x2="42" y2="56" stroke="var(--ink)" '
        'stroke-width="3.2" stroke-linecap="round"/>'),
    "camera": (
        '<rect x="8" y="18" width="48" height="34" rx="7" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<path d="M22 18 L26 11 H38 L42 18" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<circle cx="32" cy="35" r="10" fill="var(--blue)" '
        'stroke="var(--ink)" stroke-width="2.8" class="svg-pulse"/>'
        '<circle cx="48" cy="25" r="3" fill="var(--accent)"/>'),
    "play": (
        '<circle cx="32" cy="32" r="25" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<polygon points="26,20 46,32 26,44" fill="var(--paper)" '
        'stroke="var(--ink)" stroke-width="2.4" stroke-linejoin="round" '
        'class="svg-pulse"/>'),
    "book": (
        '<path d="M10 14 H28 A6 6 0 0 1 32 18 A6 6 0 0 1 36 14 H54 V50 '
        'H36 A6 6 0 0 0 32 54 A6 6 0 0 0 28 50 H10 Z" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<line x1="32" y1="18" x2="32" y2="54" stroke="var(--ink)" stroke-width="2.6"/>'
        '<line x1="16" y1="24" x2="26" y2="24" stroke="var(--accent)" stroke-width="3"/>'
        '<line x1="38" y1="24" x2="48" y2="24" stroke="var(--blue)" stroke-width="3"/>'),
    "compass": (
        '<circle cx="32" cy="32" r="24" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<g class="svg-tick">'
        '<polygon points="32,12 39,32 32,36 25,32" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.2"/>'
        '<polygon points="32,52 39,32 32,28 25,32" fill="var(--blue)" '
        'stroke="var(--ink)" stroke-width="2.2"/></g>'),
    "rocket": (
        '<g class="svg-rise">'
        '<path d="M32 6 C42 14 46 26 44 40 L32 48 L20 40 C18 26 22 14 32 6 Z" '
        'fill="var(--card)" stroke="var(--ink)" stroke-width="3" '
        'stroke-linejoin="round"/>'
        '<circle cx="32" cy="25" r="5.5" fill="var(--blue)" '
        'stroke="var(--ink)" stroke-width="2.4"/>'
        '<polygon points="20,34 10,44 20,44" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.4"/>'
        '<polygon points="44,34 54,44 44,44" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.4"/>'
        '<polygon points="27,48 32,59 37,48" fill="var(--marker)" '
        'class="svg-pulse"/></g>'),
    "coin": (
        '<g class="svg-pulse">'
        '<circle cx="32" cy="32" r="23" fill="var(--marker)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="32" cy="32" r="17" fill="none" stroke="var(--ink)" '
        'stroke-width="2" stroke-dasharray="5 4"/>'
        '<text x="32" y="40" font-size="24" text-anchor="middle" '
        'fill="var(--ink)" font-weight="900">$</text></g>'),
    "wallet": (
        '<rect x="8" y="16" width="48" height="34" rx="7" fill="var(--blue)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<rect x="38" y="27" width="18" height="12" rx="4" fill="var(--marker)" '
        'stroke="var(--ink)" stroke-width="2.6"/>'
        '<circle cx="44" cy="33" r="2.5" fill="var(--ink)"/>'),
    "coffee": (
        '<g class="svg-rise" stroke="var(--muted)" stroke-width="2.6" '
        'fill="none" stroke-linecap="round">'
        '<path d="M24 14 Q26 10 24 6"/><path d="M34 14 Q36 10 34 6"/></g>'
        '<path d="M14 20 H44 V38 A12 12 0 0 1 32 50 H26 A12 12 0 0 1 14 38 Z" '
        'fill="var(--card)" stroke="var(--ink)" stroke-width="3"/>'
        '<path d="M44 24 H50 A6 6 0 0 1 50 36 H44" fill="none" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<line x1="12" y1="54" x2="48" y2="54" stroke="var(--accent)" '
        'stroke-width="4" stroke-linecap="round"/>'),
    "lock": (
        '<path d="M21 28 V20 A11 11 0 0 1 43 20 V28" fill="none" '
        'stroke="var(--ink)" stroke-width="3.5" stroke-linecap="round"/>'
        '<rect x="13" y="28" width="38" height="28" rx="6" fill="var(--marker)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="32" cy="40" r="4" fill="var(--ink)" class="svg-pulse"/>'
        '<line x1="32" y1="44" x2="32" y2="50" stroke="var(--ink)" '
        'stroke-width="3.2" stroke-linecap="round"/>'),
    "syringe": (
        '<g class="svg-tick">'
        '<rect x="20" y="18" width="16" height="28" rx="3" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3"/>'
        '<rect x="23" y="28" width="10" height="15" fill="var(--accent)"/>'
        '<line x1="28" y1="6" x2="28" y2="18" stroke="var(--ink)" stroke-width="3.5"/>'
        '<line x1="20" y1="6" x2="36" y2="6" stroke="var(--ink)" stroke-width="3.5"/>'
        '<line x1="28" y1="46" x2="28" y2="58" stroke="var(--blue)" stroke-width="3"/></g>'),
    "stethoscope": (
        '<path d="M16 12 V28 A12 12 0 0 0 40 28 V12" fill="none" '
        'stroke="var(--ink)" stroke-width="3.5" stroke-linecap="round"/>'
        '<path d="M28 40 V48 A10 10 0 0 0 48 48 V36" fill="none" '
        'stroke="var(--blue)" stroke-width="3.5" stroke-linecap="round"/>'
        '<circle cx="48" cy="32" r="6" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.8" class="svg-pulse"/>'),
    "virus": (
        '<g class="svg-spin" stroke="var(--accent)" stroke-width="3" stroke-linecap="round">'
        '<line x1="32" y1="6" x2="32" y2="58"/><line x1="6" y1="32" x2="58" y2="32"/>'
        '<line x1="14" y1="14" x2="50" y2="50"/><line x1="50" y1="14" x2="14" y2="50"/></g>'
        '<circle cx="32" cy="32" r="15" fill="var(--accent)" stroke="var(--ink)" stroke-width="3" class="svg-pulse"/>'
        '<circle cx="27" cy="28" r="3" fill="var(--marker)"/>'
        '<circle cx="37" cy="35" r="2.5" fill="var(--card)"/>'),
    "bone": (
        '<g class="svg-tick">'
        '<path d="M18 14 A6 6 0 1 0 14 22 L42 50 A6 6 0 1 0 50 42 L22 14 A6 6 0 1 0 18 14 Z" '
        'fill="var(--card)" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/></g>'),
    "stomach": (
        '<g class="svg-pulse">'
        '<path d="M24 10 V20 C16 24 12 34 16 44 C20 54 36 56 44 48 C52 40 50 26 40 22 C34 20 32 16 32 10" '
        'fill="var(--accent)" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/></g>'),
    "neuron": (
        '<g class="svg-pulse">'
        '<polygon points="32,12 38,24 52,22 42,32 50,44 36,40 30,54 26,40 12,42 22,32 14,20 26,24" '
        'fill="var(--blue)" stroke="var(--ink)" stroke-width="2.6" stroke-linejoin="round"/></g>'
        '<circle cx="32" cy="32" r="6" fill="var(--marker)" stroke="var(--ink)" stroke-width="2.4"/>'),
    "tooth": (
        '<g class="svg-pulse">'
        '<path d="M16 18 C16 10 26 10 32 14 C38 10 48 10 48 18 C48 30 44 38 42 52 C40 56 36 52 34 42 '
        'C32 36 32 36 30 42 C28 52 24 56 22 52 C20 38 16 30 16 18 Z" '
        'fill="var(--card)" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/></g>'),
    "sleep_zzz": (
        '<g class="svg-rise" fill="none" stroke="var(--blue)" stroke-width="3.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="12,40 26,40 12,54 26,54"/>'
        '<polyline points="26,24 42,24 26,38 42,38" stroke="var(--accent)"/>'
        '<polyline points="38,8 54,8 38,22 54,22"/></g>'),
    "trend_up": (
        '<polyline points="8,46 24,32 34,40 54,18" fill="none" stroke="var(--accent)" '
        'stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round" class="svg-rise"/>'
        '<polyline points="42,18 54,18 54,30" fill="none" stroke="var(--accent)" '
        'stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>'),
    "trend_down": (
        '<polyline points="8,18 24,32 34,24 54,46" fill="none" stroke="var(--blue)" '
        'stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round" class="svg-rise"/>'
        '<polyline points="42,46 54,46 54,34" fill="none" stroke="var(--blue)" '
        'stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>'),
    "pie_chart": (
        '<circle cx="30" cy="34" r="20" fill="var(--blue)" stroke="var(--ink)" stroke-width="3"/>'
        '<path d="M32 30 L32 10 A20 20 0 0 1 52 30 Z" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round" class="svg-pulse"/>'),
    "bank": (
        '<polygon points="32,8 56,22 8,22" fill="var(--accent)" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<rect x="14" y="22" width="6" height="24" fill="var(--card)" stroke="var(--ink)" stroke-width="2.5"/>'
        '<rect x="29" y="22" width="6" height="24" fill="var(--card)" stroke="var(--ink)" stroke-width="2.5"/>'
        '<rect x="44" y="22" width="6" height="24" fill="var(--card)" stroke="var(--ink)" stroke-width="2.5"/>'
        '<rect x="8" y="46" width="48" height="8" rx="2" fill="var(--ink)"/>'),
    "cart": (
        '<g class="svg-wave">'
        '<polyline points="6,12 14,12 20,40 48,40 54,20 16,20" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
        '<circle cx="24" cy="50" r="4.5" fill="var(--accent)" stroke="var(--ink)" stroke-width="2.5"/>'
        '<circle cx="44" cy="50" r="4.5" fill="var(--accent)" stroke="var(--ink)" stroke-width="2.5"/></g>'),
    "tag_price": (
        '<g class="svg-tick">'
        '<polygon points="10,10 32,10 54,32 32,54 10,32" fill="var(--marker)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<circle cx="20" cy="20" r="4" fill="var(--card)" stroke="var(--ink)" stroke-width="2.5"/></g>'),
    "percent": (
        '<g class="svg-pulse">'
        '<line x1="14" y1="50" x2="50" y2="14" stroke="var(--ink)" stroke-width="4.5" stroke-linecap="round"/>'
        '<circle cx="19" cy="19" r="7" fill="var(--accent)" stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="45" cy="45" r="7" fill="var(--blue)" stroke="var(--ink)" stroke-width="3"/></g>'),
    "briefcase": (
        '<rect x="8" y="20" width="48" height="34" rx="6" fill="var(--card)" stroke="var(--ink)" stroke-width="3"/>'
        '<path d="M24 20 V13 A3 3 0 0 1 27 10 H37 A3 3 0 0 1 40 13 V20" fill="none" stroke="var(--ink)" stroke-width="3"/>'
        '<rect x="27" y="32" width="10" height="8" rx="2" fill="var(--accent)" stroke="var(--ink)" stroke-width="2.4" class="svg-pulse"/>'),
    "handshake": (
        '<g class="svg-pulse">'
        '<rect x="8" y="22" width="22" height="18" rx="6" fill="var(--accent)" stroke="var(--ink)" stroke-width="3"/>'
        '<rect x="34" y="22" width="22" height="18" rx="6" fill="var(--blue)" stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="32" cy="31" r="8" fill="var(--marker)" stroke="var(--ink)" stroke-width="3"/></g>'),
    "diamond": (
        '<g class="svg-pulse">'
        '<polygon points="18,12 46,12 56,26 32,54 8,26" fill="var(--blue)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<polyline points="8,26 56,26" stroke="var(--ink)" stroke-width="2.5"/>'
        '<polyline points="18,12 32,54 46,12" fill="none" stroke="var(--card)" stroke-width="2.2"/></g>'),
    "git_branch": (
        '<line x1="18" y1="14" x2="18" y2="50" stroke="var(--ink)" stroke-width="3.5"/>'
        '<path d="M18 38 C18 28 44 32 44 20" fill="none" stroke="var(--blue)" stroke-width="3.5"/>'
        '<circle cx="18" cy="14" r="5.5" fill="var(--accent)" stroke="var(--ink)" stroke-width="2.5"/>'
        '<circle cx="18" cy="50" r="5.5" fill="var(--card)" stroke="var(--ink)" stroke-width="2.5"/>'
        '<circle cx="44" cy="18" r="5.5" fill="var(--marker)" stroke="var(--ink)" stroke-width="2.5" class="svg-pulse"/>'),
    "bug": (
        '<g class="svg-pulse">'
        '<ellipse cx="32" cy="36" rx="14" ry="16" fill="var(--accent)" stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="32" cy="18" r="7" fill="var(--ink)"/>'
        '<line x1="32" y1="20" x2="32" y2="52" stroke="var(--ink)" stroke-width="3"/>'
        '<line x1="10" y1="28" x2="18" y2="32" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/>'
        '<line x1="54" y1="28" x2="46" y2="32" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/>'
        '<line x1="8" y1="40" x2="18" y2="40" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/>'
        '<line x1="56" y1="40" x2="46" y2="40" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/></g>'),
    "key": (
        '<g class="svg-tick">'
        '<circle cx="20" cy="32" r="11" fill="var(--marker)" stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="20" cy="32" r="4" fill="var(--paper)" stroke="var(--ink)" stroke-width="2.2"/>'
        '<path d="M31 32 H54 V42 M45 32 V40" fill="none" stroke="var(--ink)" stroke-width="3.5" stroke-linecap="round"/></g>'),
    "smartphone": (
        '<rect x="17" y="6" width="30" height="52" rx="6" fill="var(--card)" stroke="var(--ink)" stroke-width="3"/>'
        '<rect x="21" y="13" width="22" height="36" rx="2" fill="var(--blue)" class="svg-pulse"/>'
        '<circle cx="32" cy="53" r="2.5" fill="var(--ink)"/>'),
    "laptop": (
        '<rect x="12" y="12" width="40" height="28" rx="4" fill="var(--card)" stroke="var(--ink)" stroke-width="3"/>'
        '<rect x="16" y="16" width="32" height="20" fill="var(--blue)" class="svg-pulse"/>'
        '<polygon points="6,40 58,40 54,48 10,48" fill="var(--accent)" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'),
    "satellite": (
        '<g class="svg-tick">'
        '<rect x="25" y="25" width="14" height="14" rx="3" fill="var(--card)" stroke="var(--ink)" stroke-width="3"/>'
        '<rect x="8" y="26" width="14" height="12" fill="var(--blue)" stroke="var(--ink)" stroke-width="2.5"/>'
        '<rect x="42" y="26" width="14" height="12" fill="var(--blue)" stroke="var(--ink)" stroke-width="2.5"/>'
        '<circle cx="32" cy="46" r="4" fill="var(--accent)" class="svg-pulse"/></g>'),
    "battery": (
        '<rect x="8" y="18" width="42" height="28" rx="5" fill="var(--card)" stroke="var(--ink)" stroke-width="3"/>'
        '<rect x="50" y="26" width="6" height="12" rx="2" fill="var(--ink)"/>'
        '<rect x="13" y="23" width="28" height="18" rx="3" fill="var(--accent)" class="svg-pulse"/>'),
    "network": (
        '<g stroke="var(--ink)" stroke-width="2.8">'
        '<line x1="32" y1="14" x2="14" y2="46"/><line x1="32" y1="14" x2="50" y2="46"/>'
        '<line x1="14" y1="46" x2="50" y2="46"/></g>'
        '<circle cx="32" cy="14" r="7" fill="var(--accent)" stroke="var(--ink)" stroke-width="2.8" class="svg-pulse"/>'
        '<circle cx="14" cy="46" r="7" fill="var(--blue)" stroke="var(--ink)" stroke-width="2.8"/>'
        '<circle cx="50" cy="46" r="7" fill="var(--marker)" stroke="var(--ink)" stroke-width="2.8"/>'),
    "hourglass": (
        '<g class="svg-tick">'
        '<polygon points="16,10 48,10 32,32 48,54 16,54 32,32" fill="var(--card)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<polygon points="22,48 42,48 32,36" fill="var(--marker)"/>'
        '<line x1="12" y1="10" x2="52" y2="10" stroke="var(--ink)" stroke-width="3.5" stroke-linecap="round"/>'
        '<line x1="12" y1="54" x2="52" y2="54" stroke="var(--ink)" stroke-width="3.5" stroke-linecap="round"/></g>'),
    "scroll": (
        '<rect x="14" y="10" width="36" height="44" rx="5" fill="var(--card)" stroke="var(--ink)" stroke-width="3"/>'
        '<line x1="22" y1="22" x2="42" y2="22" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/>'
        '<line x1="22" y1="32" x2="42" y2="32" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/>'
        '<line x1="22" y1="42" x2="34" y2="42" stroke="var(--blue)" stroke-width="3" stroke-linecap="round"/>'),
    "crown": (
        '<g class="svg-rise">'
        '<polygon points="8,48 12,20 25,32 32,14 39,32 52,20 56,48" fill="var(--marker)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<circle cx="32" cy="12" r="3.5" fill="var(--accent)"/></g>'),
    "anchor": (
        '<g class="svg-tick" fill="none" stroke="var(--ink)" stroke-width="3.5" stroke-linecap="round">'
        '<circle cx="32" cy="14" r="5" fill="var(--marker)"/>'
        '<line x1="32" y1="19" x2="32" y2="54"/>'
        '<line x1="22" y1="28" x2="42" y2="28"/>'
        '<path d="M14 40 A18 18 0 0 0 50 40" stroke="var(--blue)"/></g>'),
    "map_pin": (
        '<g class="svg-rise">'
        '<path d="M32 6 C20 6 12 15 12 26 C12 40 32 58 32 58 C32 58 52 40 52 26 C52 15 44 6 32 6 Z" '
        'fill="var(--accent)" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<circle cx="32" cy="25" r="7" fill="var(--card)" stroke="var(--ink)" stroke-width="2.5"/></g>'),
    "mountain": (
        '<polygon points="6,52 26,16 44,52" fill="var(--blue)" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<polygon points="28,52 44,24 58,52" fill="var(--accent)" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<circle cx="48" cy="14" r="5" fill="var(--marker)" class="svg-pulse"/>'),
    "tree": (
        '<rect x="28" y="40" width="8" height="16" fill="var(--ink)"/>'
        '<circle cx="32" cy="26" r="16" fill="var(--blue)" stroke="var(--ink)" stroke-width="3" class="svg-pulse"/>'
        '<circle cx="24" cy="22" r="5" fill="var(--marker)"/>'),
    "telescope": (
        '<g class="svg-tick">'
        '<polygon points="10,28 50,14 54,26 14,36" fill="var(--blue)" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<line x1="28" y1="32" x2="18" y2="54" stroke="var(--ink)" stroke-width="3.2" stroke-linecap="round"/>'
        '<line x1="28" y1="32" x2="38" y2="54" stroke="var(--ink)" stroke-width="3.2" stroke-linecap="round"/></g>'),
    "megaphone": (
        '<g class="svg-pulse">'
        '<polygon points="12,26 24,26 48,14 48,50 24,38 12,38" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<path d="M16 38 L20 52 H26 L23 38" fill="var(--marker)" stroke="var(--ink)" stroke-width="2.5"/></g>'),
    "chat_bubble": (
        '<g class="svg-pulse">'
        '<path d="M10 14 H54 A4 4 0 0 1 58 18 V42 A4 4 0 0 1 54 46 H26 L14 56 V46 H10 A4 4 0 0 1 6 42 V18 A4 4 0 0 1 10 14 Z" '
        'fill="var(--card)" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/>'
        '<circle cx="22" cy="30" r="3.5" fill="var(--accent)"/>'
        '<circle cx="32" cy="30" r="3.5" fill="var(--blue)"/>'
        '<circle cx="42" cy="30" r="3.5" fill="var(--marker)"/></g>'),
    "puzzle": (
        '<g class="svg-pulse">'
        '<rect x="14" y="14" width="36" height="36" rx="6" fill="var(--marker)" stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="32" cy="14" r="6" fill="var(--marker)" stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="50" cy="32" r="6" fill="var(--marker)" stroke="var(--ink)" stroke-width="3"/></g>'),
    "flag": (
        '<line x1="14" y1="8" x2="14" y2="56" stroke="var(--ink)" stroke-width="3.5" stroke-linecap="round"/>'
        '<path d="M14 10 Q28 6 36 12 T54 12 V34 Q40 38 32 32 T14 34 Z" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="3" stroke-linejoin="round" class="svg-wave"/>'),
}


def list_icons() -> list[str]:
    """Return sorted list of all available icon names."""
    return sorted(ICONS)


def search_icons(query: str) -> list[str]:
    """Case-insensitive substring search over available icon names."""
    q = str(query or "").strip().lower()
    if not q:
        return list_icons()
    return [name for name in sorted(ICONS) if q in name]


def icon_svg(name: str, size: int = 120) -> str:
    """Inline SVG for a named icon (unknown names -> question mark)."""
    inner = ICONS.get(name)
    if inner is None:
        return (f'<svg class="ambient-icon" viewBox="0 0 64 64" '
                f'width="{size}" height="{size}">'
                '<text x="32" y="46" font-size="44" text-anchor="middle" '
                'fill="var(--accent)" font-weight="900">؟</text></svg>')
    return (f'<svg class="ambient-icon" viewBox="0 0 64 64" '
            f'width="{size}" height="{size}">'
            f"{inner}</svg>")


# Composite multi-layer SVG scene illustrations (viewBox 0 0 320 220)
# Each layer carries deterministic inner motion classes driven by window.setTime(t).
ILLUSTRATIONS: dict[str, str] = {
    "thermoregulation": (
        '<rect x="16" y="18" width="288" height="184" rx="24" fill="var(--card)" stroke="var(--ink)" stroke-width="3"/>'
        '<g class="svg-wave"><circle cx="78" cy="110" r="42" fill="var(--blue)" opacity="0.18"/>'
        '<path d="M48 95 H108 M48 110 H108 M48 125 H108" stroke="var(--blue)" stroke-width="4" stroke-linecap="round"/></g>'
        '<g class="svg-pulse"><circle cx="160" cy="110" r="46" fill="var(--accent)" stroke="var(--ink)" stroke-width="3"/>'
        '<text x="160" y="120" font-size="28" text-anchor="middle" fill="var(--paper)" font-weight="900">37°</text></g>'
        '<g class="svg-rise"><rect x="228" y="64" width="20" height="92" rx="10" fill="var(--marker)" stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="238" cy="148" r="14" fill="var(--accent)" stroke="var(--ink)" stroke-width="3"/></g>'
    ),
    "neural_signal": (
        '<rect x="16" y="18" width="288" height="184" rx="24" fill="var(--card)" stroke="var(--ink)" stroke-width="3"/>'
        '<line x1="64" y1="110" x2="256" y2="110" stroke="var(--ink)" stroke-width="4" stroke-dasharray="10 8"/>'
        '<g class="svg-pulse"><circle cx="72" cy="110" r="34" fill="var(--blue)" stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="248" cy="110" r="34" fill="var(--accent)" stroke="var(--ink)" stroke-width="3"/></g>'
        '<g class="svg-wave"><circle cx="160" cy="110" r="14" fill="var(--marker)" stroke="var(--ink)" stroke-width="3"/></g>'
    ),
    "data_pipeline": (
        '<rect x="16" y="18" width="288" height="184" rx="24" fill="var(--card)" stroke="var(--ink)" stroke-width="3"/>'
        '<g class="svg-rise"><rect x="38" y="74" width="64" height="72" rx="12" fill="var(--blue)" stroke="var(--ink)" stroke-width="3"/></g>'
        '<g class="svg-pulse"><rect x="128" y="62" width="64" height="96" rx="12" fill="var(--marker)" stroke="var(--ink)" stroke-width="3"/></g>'
        '<g class="svg-rise"><rect x="218" y="74" width="64" height="72" rx="12" fill="var(--accent)" stroke="var(--ink)" stroke-width="3"/></g>'
    ),
    "growth_engine": (
        '<rect x="16" y="18" width="288" height="184" rx="24" fill="var(--card)" stroke="var(--ink)" stroke-width="3"/>'
        '<g class="svg-spin"><circle cx="105" cy="115" r="38" fill="none" stroke="var(--ink)" stroke-width="8" stroke-dasharray="10 7"/>'
        '<circle cx="105" cy="115" r="24" fill="var(--blue)" stroke="var(--ink)" stroke-width="3"/></g>'
        '<g class="svg-rise"><polyline points="165,155 198,118 222,130 268,72" fill="none" stroke="var(--accent)" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/></g>'
    ),
    "balance_system": (
        '<rect x="16" y="18" width="288" height="184" rx="24" fill="var(--card)" stroke="var(--ink)" stroke-width="3"/>'
        '<line x1="160" y1="48" x2="160" y2="174" stroke="var(--ink)" stroke-width="5" stroke-linecap="round"/>'
        '<g class="svg-tick"><line x1="68" y1="86" x2="252" y2="86" stroke="var(--ink)" stroke-width="5" stroke-linecap="round"/>'
        '<circle cx="84" cy="126" r="24" fill="var(--blue)" stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="236" cy="126" r="24" fill="var(--accent)" stroke="var(--ink)" stroke-width="3"/></g>'
    ),
    "experiment_lab": (
        '<rect x="16" y="18" width="288" height="184" rx="24" fill="var(--card)" stroke="var(--ink)" stroke-width="3"/>'
        '<g class="svg-pulse"><polygon points="105,54 145,154 65,154" fill="var(--blue)" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/></g>'
        '<g class="svg-rise"><circle cx="210" cy="112" r="38" fill="var(--marker)" stroke="var(--ink)" stroke-width="3"/>'
        '<circle cx="210" cy="112" r="16" fill="var(--accent)" stroke="var(--ink)" stroke-width="2.5"/></g>'
    ),
}


def list_illustrations() -> list[str]:
    """Return sorted list of composite SVG scene illustration names."""
    return sorted(ILLUSTRATIONS)


def illustration_svg(name: str, width: int = 320, height: int = 220) -> str:
    """Render a multi-layer animated composite SVG scene illustration."""
    inner = ILLUSTRATIONS.get(name)
    if inner is None:
        return icon_svg(name, min(width, height))
    return (
        f'<svg class="ambient-icon composite-ill" viewBox="0 0 320 220" '
        f'width="{width}" height="{height}">{inner}</svg>'
    )
