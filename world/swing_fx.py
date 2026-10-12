"""
Lightweight, pooled visual FX for melee arcs and cones.

Drawn as a filled pie-slice on a reusable full-screen SRCALPHA surface
so we never allocate a new Surface per frame. One list of pending FX,
each with a short lifetime; Game owns the list and ticks it.
"""
import math


def spawn_swing(fx_list, x, y, facing, profile):
    half = math.radians(profile.arc_degrees / 2.0)
    angle = math.atan2(facing.y, facing.x)
    fx_list.append({
        "x": x, "y": y,
        "angle": angle,
        "arc_deg": profile.arc_degrees,
        "range": profile.range,
        "kind": profile.kind,           # "arc" or "cone_aoe"
        "life": 0.20, "max_life": 0.20,
    })


def tick(fx_list, dt):
    for fx in fx_list:
        fx["life"] -= dt
    fx_list[:] = [fx for fx in fx_list if fx["life"] > 0]


def build_polygon_points(fx, world_to_screen, steps=18):
    """Build screen-space polygon points for a pie slice."""
    cx, cy = fx["x"], fx["y"]
    half = math.radians(fx["arc_deg"] / 2.0)
    a0 = fx["angle"] - half
    a1 = fx["angle"] + half
    r = fx["range"]
    pts = [world_to_screen((cx, cy))]
    for i in range(steps + 1):
        t = i / steps
        a = a0 + (a1 - a0) * t
        pts.append(world_to_screen((cx + math.cos(a) * r, cy + math.sin(a) * r)))
    return pts