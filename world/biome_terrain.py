"""Biome-specific terrain profiles.

Each biome maps to a list of weighted terrain surface specifications.
Phase 2: consumed by world/expansion.attach_extension to seed pending
terrain surfaces into extension rooms.
"""
from __future__ import annotations
import random
from typing import Iterable, List, Optional


BIOME_TERRAIN: dict[str, list[dict]] = {
    "ash_canyon": [
        {"kind": "fire", "weight": 6, "radius": (60, 100), "duration": 10.0, "power": 8},
        {"kind": "rot",  "weight": 2, "radius": (55, 80),  "duration": 8.0,  "power": 5},
    ],
    "sunken_library": [
        {"kind": "water",  "weight": 7, "radius": (70, 120), "duration": 20.0, "power": 6},
        {"kind": "static", "weight": 1, "radius": (50, 80),  "duration": 6.0,  "power": 8},
    ],
    "corrupted_hollow": [
        {"kind": "blight", "weight": 9, "radius": (60, 110), "duration": 14.0, "power": 7},
    ],
    "frozen_reach": [
        {"kind": "ice",   "weight": 8, "radius": (70, 120), "duration": 15.0, "power": 5},
        {"kind": "water", "weight": 2, "radius": (60, 90),  "duration": 20.0, "power": 4},
    ],
    "storm_peaks": [
        {"kind": "static", "weight": 6, "radius": (55, 90),  "duration": 8.0,  "power": 9},
        {"kind": "water",  "weight": 3, "radius": (60, 100), "duration": 18.0, "power": 5},
    ],
    "bone_wastes": [
        {"kind": "rot", "weight": 9, "radius": (55, 95), "duration": 12.0, "power": 6},
    ],
    "verdant_ruin": [
        {"kind": "rot",   "weight": 6, "radius": (70, 110), "duration": 12.0, "power": 5},
        {"kind": "water", "weight": 4, "radius": (60, 100), "duration": 20.0, "power": 4},
    ],
}


def biome_profile(biome: Optional[str]) -> list[dict]:
    return BIOME_TERRAIN.get((biome or "").lower(), [])


def _weighted_pick(rng: random.Random, profile: list[dict]) -> Optional[dict]:
    if not profile:
        return None
    total = sum(p.get("weight", 1) for p in profile)
    if total <= 0:
        return profile[0]
    r = rng.uniform(0, total)
    acc = 0.0
    for p in profile:
        acc += p.get("weight", 1)
        if r <= acc:
            return p
    return profile[-1]


def biome_terrain_surfaces(biome: Optional[str], room_rect: tuple[int, int, int, int],
                           tile_size: int, count: int, rng: random.Random,
                           floor_check=None) -> List[dict]:
    """Produce `count` terrain-surface dicts inside `room_rect` (tile coords).

    `floor_check(c, r)` -> bool lets the caller reject tiles that are walls.
    Returned dicts use *world* pixel coordinates for x/y.
    """
    profile = biome_profile(biome)
    if not profile or count <= 0:
        return []

    x0, y0, x1, y1 = room_rect
    pad = 2
    surfaces: list[dict] = []
    attempts = count * 6
    for _ in range(attempts):
        if len(surfaces) >= count:
            break
        entry = _weighted_pick(rng, profile)
        if entry is None:
            break
        cx = rng.randint(x0 + pad, max(x0 + pad, x1 - pad - 1))
        cy = rng.randint(y0 + pad, max(y0 + pad, y1 - pad - 1))
        if floor_check is not None and not floor_check(cx, cy):
            continue
        rmin, rmax = entry["radius"]
        radius = float(rng.uniform(rmin, rmax))
        surfaces.append({
            "x": cx * tile_size + tile_size / 2,
            "y": cy * tile_size + tile_size / 2,
            "radius": radius,
            "kind": entry["kind"],
            "duration": float(entry["duration"]),
            "power": int(entry["power"]),
        })
    return surfaces