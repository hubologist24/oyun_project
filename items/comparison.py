"""
Stat comparison for hover-tooltip gear comparison, per spec section 3.
Pure function -- also exactly what test_helpers.test_inventory_compare()
will call later.
"""
from typing import List, Dict


STAT_LABELS = {
    "damage": "Damage",
    "armor": "Armor",
    "life": "Life",
    "attack_speed": "Attack Speed",
    "movement_speed": "Movement Speed",
    "critical_chance": "Critical Chance",
}


def _snapshot(item) -> Dict[str, float]:
    if item is None:
        return {}
    snap = {}
    if getattr(item, "damage_bonus", 0):
        snap["damage"] = item.damage_bonus
    if getattr(item, "armor_bonus", 0):
        snap["armor"] = item.armor_bonus
    for modifier_id, value in getattr(item, "_other_bonuses", {}).items():
        snap[modifier_id] = value
    for dtype, value in getattr(item, "_resist_bonus", {}).items():
        snap[f"resist_{dtype}"] = value
    return snap


def compare_items(candidate, equipped=None) -> List[dict]:
    a = _snapshot(candidate)
    b = _snapshot(equipped)
    keys = set(a) | set(b)
    diffs = []
    for key in keys:
        delta = a.get(key, 0.0) - b.get(key, 0.0)
        if abs(delta) < 1e-6:
            continue
        diffs.append({
            "stat": key,
            "label": STAT_LABELS.get(key, key.replace("_", " ").title()),
            "delta": round(delta, 1),
            "is_positive": delta > 0,
        })
    diffs.sort(key=lambda d: d["label"])
    return diffs


def format_diff_line(diff: dict) -> str:
    sign = "+" if diff["is_positive"] else ""
    return f"{sign}{diff['delta']} {diff['label']}"