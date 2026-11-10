#!/usr/bin/env python
"""
tools/map_bot.py  --  Level 1 map-testing bot (no combat).

Generates regions with the REAL generator (world.map_generator.build_procedural_area),
walks them with BFS like a player would, and rejects bad maps.

Checks
  * every mandatory room (entrance / guardian / reward / gate) is reachable
  * guardian exists, is reachable, and is a real chokepoint (no bypass)
  * every exit gate (simulated exactly like World.spawn_exit_gates) is
    accessible, on a side attach_extension supports, not duplicated
  * disconnected corridors / orphaned rooms / empty chambers
  * pointless dead ends (empty leaf rooms, stub corridors)
  * entrance -> exit path length and tortuosity
  * each exit gate's seal objective is actually completable on this map
    (and the region is not soft-locked)

Usage (from repo root, with an empty tools/__init__.py present)
  python -m tools.map_bot --count 5000 --seed 1
  python -m tools.map_bot --count 20000 --workers 8 --report map_report.json --dump-dir map_failures
  python -m tools.map_bot --replay 123456789
  python -m tools.map_bot --count 3000 --archetype labyrinth --level 40
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
import traceback
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from world.gate_seals import roll_seal_type, build_seal, calibrate_seal


import core.config as config                       # noqa: E402
import world.map_generator as mapgen               # noqa: E402
from world.room_graph import (                     # noqa: E402
    RoomType, LayoutArchetype, build_room_graph, BIOME_ARCHETYPE_WEIGHTS,
)
from world.gate_seals import roll_seal_type, build_seal                    # noqa: E402
from world.gate_tiers import gate_count_for_region, assign_tiers_for_gate_count  # noqa: E402

try:
    from world.expansion import ENEMY_TILES_PER_MOB, ENEMY_DENSITY_CAP, ENEMY_ROOM_MAX
except Exception:  # keep the bot usable even if enemies/* can't import
    ENEMY_TILES_PER_MOB, ENEMY_DENSITY_CAP, ENEMY_ROOM_MAX = 14, 3.5, 20

try:
    from core.difficulty import area_density_mult, D as _D
except Exception:
    _D = None

    def area_density_mult(area_level):
        return min(5.0, max(0.8, 1.0 + (area_level - 3) * 0.20))
    


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
SPAWNER_HOSTS = {RoomType.COMBAT, RoomType.ELITE, RoomType.HAZARD, RoomType.CHALLENGE}

FATAL, ERROR, WARN = "FATAL", "ERROR", "WARN"
_SEV_ORDER = {FATAL: 0, ERROR: 1, WARN: 2}

N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
INWARD = {"e": (-1, 0), "w": (1, 0), "s": (0, -1), "n": (0, 1)}
OUTWARD = {k: (-dx, -dy) for k, (dx, dy) in INWARD.items()}
ATTACHABLE_SIDES = ("e", "s")          # world.expansion.attach_extension only handles these

MANDATORY_TYPES = {RoomType.ENTRANCE, RoomType.GUARDIAN, RoomType.REWARD, RoomType.GATE}
POINTLESS_LEAF_TYPES = {RoomType.COMBAT, RoomType.HAZARD}
# rooms whose interactable is recorded in world.used_shrines (what the "shrines" seal counts).
# RestShrine.interact never touches used_shrines, so SHRINE rooms do NOT count.
CHEST_TYPES = {RoomType.TREASURE, RoomType.REWARD, RoomType.SECRET}

SPAWNER_COUNT = 3                      # expansion._spawn_spawner_room always spawns 3
SPAWNER_OFFSETS = ((0, -90), (-90, 70), (90, 70))
SURVIVAL_RING = (200.0, 320.0)         # ExtensionMixin._spawn_survival_wave distance band
SEAL_RADIUS = {"bargain": 220, "survival": 420, "trial": 500, "corruption": 800}

ROOM_CHAR = {
    RoomType.ENTRANCE: "S", RoomType.COMBAT: "c", RoomType.ELITE: "E",
    RoomType.TREASURE: "T", RoomType.SHRINE: "+", RoomType.EVENT: "?",
    RoomType.HAZARD: "h", RoomType.CHALLENGE: "!", RoomType.SECRET: "$",
    RoomType.GUARDIAN: "B", RoomType.GATE: "X", RoomType.REWARD: "R",
}
LEGEND = ("legend: # wall  . room floor  : corridor  o orphan-room floor  "
          "~ UNREACHABLE floor  1-3 exit gates  "
          "S entrance B guardian R reward X gate c combat E elite T treasure "
          "+ shrine ? event h hazard ! challenge $ secret")

BIOMES = sorted(BIOME_ARCHETYPE_WEIGHTS) + ["uncharted", "anomaly_wastes"]


# ---------------------------------------------------------------------------
# config / result types
# ---------------------------------------------------------------------------
@dataclass
class BotConfig:
    max_path_factor: float = 2.5       # ERROR if path_tiles > factor * (cols + rows)
    min_path_factor: float = 0.12      # WARN  if path_tiles < factor * (cols + rows)
    max_tortuosity: float = 4.0        # WARN  if path / manhattan > this
    max_pointless_dead_ends: int = 3   # ERROR if more pointless leaves than this
    min_arena_tiles: int = 10          # survival seal: free floor tiles in spawn ring
    reject_on_error: bool = True
    max_warnings: int = 6              # reject if more WARNs than this
    elite_chance: Optional[float] = None   # None -> D.get("elite_chance")
    render: str = "rejected"           # "never" | "rejected" | "always"


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    data: dict = field(default_factory=dict)

    def to_dict(self):
        return {"severity": self.severity, "code": self.code,
                "message": self.message, "data": self.data}


@dataclass
class SimGate:
    index: int
    tier: str
    col: int
    row: int
    side: str
    seal: dict
    seal_level: int
    approach: Optional[Tuple[int, int]] = None
    status: str = "unchecked"
    detail: str = ""

    @property
    def label(self):
        return f"gate#{self.index + 1}[{self.tier}/{self.side}/{self.seal.get('type')}]"

    def to_dict(self):
        return {"index": self.index, "tier": self.tier, "tile": [self.col, self.row],
                "side": self.side, "seal": self.seal.get("type"),
                "target": self.seal.get("target"), "seal_level": self.seal_level,
                "approach": list(self.approach) if self.approach else None,
                "status": self.status, "detail": self.detail}


@dataclass
class MapReport:
    seed: Optional[int] = None
    params: dict = field(default_factory=dict)
    archetype: str = "?"
    issues: List[Issue] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    gates: List[dict] = field(default_factory=list)
    accepted: bool = False
    ascii: Optional[str] = None

    def add(self, severity, code, message, **data):
        self.issues.append(Issue(severity, code, message, data))

    def count(self, severity):
        return sum(1 for i in self.issues if i.severity == severity)

    def codes(self):
        return sorted({(i.severity, i.code) for i in self.issues},
                      key=lambda k: (_SEV_ORDER[k[0]], k[1]))

    def score(self):
        return (self.count(FATAL), self.count(ERROR), self.count(WARN))

    def finalize(self, cfg: BotConfig):
        self.issues.sort(key=lambda i: _SEV_ORDER[i.severity])
        self.accepted = (self.count(FATAL) == 0
                         and not (cfg.reject_on_error and self.count(ERROR))
                         and self.count(WARN) <= cfg.max_warnings)
        return self

    def to_dict(self):
        return {"seed": self.seed, "params": self.params, "archetype": self.archetype,
                "accepted": self.accepted, "metrics": self.metrics, "gates": self.gates,
                "issues": [i.to_dict() for i in self.issues], "ascii": self.ascii}


# ---------------------------------------------------------------------------
# grid helpers
# ---------------------------------------------------------------------------
def _derive_seed(base, name) -> int:
    d = hashlib.sha256(f"{base}:{name}".encode("utf-8")).digest()
    return int.from_bytes(d[:4], "big")


def _bfs(grid, starts, blocked=frozenset()) -> Dict[Tuple[int, int], int]:
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    dist, q = {}, deque()
    for s in starts:
        c, r = s
        if (0 <= r < rows and 0 <= c < cols and grid[r][c] == 0
                and s not in blocked and s not in dist):
            dist[s] = 0
            q.append(s)
    while q:
        c, r = q.popleft()
        d = dist[(c, r)] + 1
        for dc, dr in N4:
            nc, nr = c + dc, r + dr
            if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == 0:
                t = (nc, nr)
                if t not in dist and t not in blocked:
                    dist[t] = d
                    q.append(t)
    return dist


def _components(tiles) -> List[List[Tuple[int, int]]]:
    pool = set(tiles)
    comps = []
    while pool:
        seed = pool.pop()
        comp, stack = [seed], [seed]
        while stack:
            c, r = stack.pop()
            for dc, dr in N4:
                t = (c + dc, r + dr)
                if t in pool:
                    pool.remove(t)
                    comp.append(t)
                    stack.append(t)
        comps.append(comp)
    return comps


def _rect_tiles(rect, rows, cols):
    x0, y0, x1, y1 = rect
    for r in range(max(0, y0), min(rows, y1)):
        for c in range(max(0, x0), min(cols, x1)):
            yield (c, r)


def _in_rect(t, rect):
    return rect[0] <= t[0] < rect[2] and rect[1] <= t[1] < rect[3]


def _center(rect):
    return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)


def _probe(grid, rect):
    """A floor tile representative of a room (center if possible)."""
    rows, cols = len(grid), len(grid[0])
    c, r = _center(rect)
    if 0 <= r < rows and 0 <= c < cols and grid[r][c] == 0:
        return (c, r)
    for t in _rect_tiles(rect, rows, cols):
        if grid[t[1]][t[0]] == 0:
            return t
    return (c, r)


def _passable(grid, c, r):
    return 0 <= r < len(grid) and 0 <= c < len(grid[0]) and grid[r][c] == 0


def estimate_room_enemies(rect, rtype, area_level) -> Tuple[int, int]:
    """Upper-bound (count, guaranteed_elites) mirroring world.expansion.attach_extension."""
    density = min(ENEMY_DENSITY_CAP, area_density_mult(area_level))
    rw, rh = rect[2] - rect[0], rect[3] - rect[1]
    base = max(2, min(ENEMY_ROOM_MAX, int(round((rw * rh) / ENEMY_TILES_PER_MOB * density))))
    if rtype == RoomType.COMBAT:
        return base, 0
    if rtype == RoomType.ELITE:
        return 1 + max(1, base // 2), 1
    if rtype == RoomType.CHALLENGE:
        return base + 2, 0
    if rtype == RoomType.HAZARD:
        return max(2, base - 1), 0
    return 0, 0


# ---------------------------------------------------------------------------
# exit-gate simulation (mirrors World.spawn_exit_gates_for_extension)
# ---------------------------------------------------------------------------
def simulate_exit_gates(grid, gate_room, area_level, size_tier, seal_rng_factory, ext_id):
    want = gate_count_for_region(area_level, size_tier)
    sites = mapgen.plan_exit_gate_sites(grid, gate_room, want)
    tiers = assign_tiers_for_gate_count(len(sites))
    gates = []
    for i, (tier, (col, row, side)) in enumerate(zip(tiers, sites)):
        seal_rng = seal_rng_factory(f"gate_seal:{ext_id}:{i}:{tier['id']}")
        seal_level = area_level + tier["level_offset"]
        seal = build_seal(roll_seal_type(seal_rng, area_level=seal_level), seal_level, seal_rng)
        gates.append(SimGate(i, tier["id"], col, row, side, seal, seal_level))
    return gates


# ---------------------------------------------------------------------------
# seal objective checks
# ---------------------------------------------------------------------------
@dataclass
class _Ctx:
    grid: list
    reach: dict
    rooms: list
    types: list
    room_reach: list
    room_enemies: list
    guardian_ok: bool
    elite_chance: float
    explore_frac: float
    min_arena_tiles: int


def _enemies_near(ctx, gx, gy, radius_px):
    ts = config.TILE_SIZE
    total = 0
    for rect, (cnt, _) in zip(ctx.rooms, ctx.room_enemies):
        if not cnt:
            continue
        cx = (rect[0] + rect[2]) / 2 * ts
        cy = (rect[1] + rect[3]) / 2 * ts
        half_diag = math.hypot(rect[2] - rect[0], rect[3] - rect[1]) / 2 * ts
        if math.hypot(cx - gx, cy - gy) - half_diag <= radius_px:
            total += cnt
    return total


def _line_clear(grid, x0, y0, x1, y1, step=8.0):
    ts = config.TILE_SIZE
    n = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
    for k in range(n + 1):
        t = k / n
        x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        if not _passable(grid, int(x // ts), int(y // ts)):
            return False
    return True


def _check_seal(g: SimGate, ctx: _Ctx) -> Tuple[str, str]:
    ts = config.TILE_SIZE
    gx, gy = (g.col + 0.5) * ts, (g.row + 0.5) * ts
    total = sum(c for c, _ in ctx.room_enemies)
    sure_elites = sum(e for _, e in ctx.room_enemies)
    exp_elites = int(sure_elites + ctx.elite_chance * max(0, total - sure_elites))
    reach = [i for i, ok in enumerate(ctx.room_reach) if ok]
    sure = sum(1 for i in reach if ctx.types[i] in CHEST_TYPES or ctx.types[i] == RoomType.SHRINE)
    events = sum(1 for i in reach if ctx.types[i] == RoomType.EVENT)
    # expansion always adds a fallback rest shrine + chest when a region has none
    interact = max(sure + (2 * events) // 3, 2 if len(reach) > 1 else 0)
    hosts = [i for i in reach if ctx.types[i] in SPAWNER_HOSTS]

    g.seal = calibrate_seal(dict(g.seal), enemies=total,
                            elites=max(1, exp_elites) if total else 0,
                            spawners=g.seal.get("target", 1) if hosts else 0,
                            interactables=interact)
    st, target = g.seal.get("type"), g.seal.get("target", 1)

    if st == "boss":
        return ("ok", "") if ctx.guardian_ok else ("impossible", "boss seal but no reachable guardian")
    if st == "hunt_nemesis":
        return ("ok", "") if ctx.guardian_ok else ("risky", "no guardian; needs a visiting nemesis")
    if st == "shrines":
        return ("ok", "") if interact >= target else ("impossible", f"{interact} interactables < {target}")
    if st in ("purge", "elite_hunt"):
        return ("ok", "") if total else ("impossible", "region spawns no enemies")
    if st == "destroy_spawners":
        return ("ok", "") if hosts else ("impossible", "no reachable room can host spawners")
    if st == "survival":
        lo, hi = SURVIVAL_RING
        rad = int(hi // ts) + 2
        ring = sum(1 for r in range(g.row - rad, g.row + rad + 1)
                   for c in range(g.col - rad, g.col + rad + 1)
                   if (c, r) in ctx.reach
                   and lo <= math.hypot((c + 0.5) * ts - gx, (r + 0.5) * ts - gy) <= hi)
        if ring == 0:
            return "impossible", "no floor in the wave-spawn ring around the gate"
        if ring < ctx.min_arena_tiles:
            return "risky", f"only {ring} spawn tiles in wave ring"
        return "ok", ""
    if st in ("trial", "corruption"):
        near = _enemies_near(ctx, gx, gy, SEAL_RADIUS[st])
        if near < target:
            return "risky", f"only ~{near} enemies within {SEAL_RADIUS[st]}px of {target}; player must lure"
        return "ok", ""
    if st == "bargain":
        return "ok", ""
    if st == "exploration":
        if ctx.explore_frac < target:
            return "impossible", f"max exploration {ctx.explore_frac:.0%} < {target:.0%}"
        return "ok", ""
    if st == "hunt_convoy":
        if len(ctx.rooms) < 2:
            return "impossible", "convoy needs at least 2 rooms"
        a, b = ctx.rooms[0], ctx.rooms[-1]
        if not _line_clear(ctx.grid, (a[0] + a[2]) / 2 * ts, (a[1] + a[3]) / 2 * ts,
                           (b[0] + b[2]) / 2 * ts, (b[1] + b[3]) / 2 * ts):
            return "risky", "convoy waypoint line crosses walls (OK only if ConvoyCarrier pathfinds)"
        return "ok", ""
    return "risky", f"unknown seal type '{st}'"


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def _render(grid, reach, owner, rooms, types, gates) -> str:
    rows, cols = len(grid), len(grid[0])
    canvas = []
    for r in range(rows):
        line = []
        for c in range(cols):
            if grid[r][c] != 0:
                ch = "#"
            elif (c, r) not in reach:
                ch = "~"
            else:
                o = owner.get((c, r))
                ch = "o" if (o and o[0] == "orphan") else ("." if o else ":")
            line.append(ch)
        canvas.append(line)
    for rect, t in zip(rooms, types):
        c, r = _center(rect)
        if 0 <= r < rows and 0 <= c < cols:
            canvas[r][c] = ROOM_CHAR.get(t, "?")
    for g in gates:
        if 0 <= g.row < rows and 0 <= g.col < cols:
            canvas[g.row][g.col] = str(g.index + 1)
    return "\n".join("".join(l) for l in canvas) + "\n" + LEGEND


# ---------------------------------------------------------------------------
# the validator
# ---------------------------------------------------------------------------
def validate_area(area, *, all_rects=None, area_level=1, size_tier=-1,
                  has_guardian=True, seal_rng_factory=None, ext_id="bot_ext",
                  cfg: Optional[BotConfig] = None, params=None, seed=None) -> MapReport:
    cfg = cfg or BotConfig()
    if seal_rng_factory is None:
        seal_rng_factory = lambda name: random.Random(_derive_seed(0, name))  # noqa: E731
    ts = config.TILE_SIZE
    rep = MapReport(seed=seed, params=dict(params or {}),
                    archetype=str(area.get("archetype", "?")))
    add, m = rep.add, rep.metrics

    tm = area.get("tilemap")
    grid = tm.grid if tm is not None else []
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    rooms = [tuple(r) for r in (area.get("rooms") or [])]
    types = [RoomType(t) for t in (area.get("room_types") or [])][:len(rooms)]
    types += [RoomType.COMBAT] * (len(rooms) - len(types))
    m.update(cols=cols, rows=rows, placed_rooms=len(rooms), area_level=area_level)

    if not rooms or not cols:
        add(FATAL, "NO_ROOMS", "generator produced no usable rooms")
        return rep.finalize(cfg)

    # -- 1. graph -> rect placement ------------------------------------------
    graph = area.get("room_graph")
    placed = area.get("node_to_room") or {}
    if graph is not None:
        dropped = [str(n.room_type) for n in graph.nodes if n.index not in placed]
        m.update(graph_nodes=len(graph.nodes), dropped_nodes=len(dropped))
        if dropped:
            critical = [t for t in dropped if RoomType(t) in MANDATORY_TYPES]
            add(ERROR if critical else WARN, "GRAPH_NODES_DROPPED",
                f"{len(dropped)}/{len(graph.nodes)} graph nodes got no room rect "
                f"(map too small for layout): {dropped}", dropped=dropped)

    entrance = tuple(area.get("start_room") or rooms[0])
    gate_room = tuple(area.get("gate_room") or rooms[-1])
    boss_room = tuple(area["boss_room"]) if area.get("boss_room") else None

    
    #for i, r in enumerate(rooms):
    #    room_idx.setdefault(r, i)
    #orphans = [tuple(r) for r in (all_rects or []) if tuple(r) not in room_idx]
    #m["orphan_rects"] = len(orphans)
    room_idx = {}
    for i, r in enumerate(rooms):
        room_idx.setdefault(r, i)

    def _mostly_floor(rect):
        tiles = list(_rect_tiles(rect, rows, cols))
        return bool(tiles) and sum(grid[r][c] == 0 for c, r in tiles) > 0.8 * len(tiles)

    orphans = [tuple(r) for r in (all_rects or [])
               if tuple(r) not in room_idx and _mostly_floor(tuple(r))]
    if area.get("pruned_nodes"):
        add(WARN, "GRAPH_PRUNED", f"{area['pruned_nodes']} optional rooms pruned to fit the map")
    
    m["orphan_rects"] = len(orphans)

    owner = {}
    for i, r in enumerate(rooms):
        for t in _rect_tiles(r, rows, cols):
            owner.setdefault(t, ("room", i))
    for j, r in enumerate(orphans):
        for t in _rect_tiles(r, rows, cols):
            owner.setdefault(t, ("orphan", j))

    if entrance == gate_room:
        add(FATAL, "ENTRANCE_IS_GATE", "entrance and gate are the same room")

    # -- 2. walk the map -------------------------------------------------------
    start = _probe(grid, entrance)
    reach = _bfs(grid, [start])
    if not reach:
        add(FATAL, "ENTRANCE_BLOCKED", "entrance room has no walkable floor")
        return rep.finalize(cfg)

    room_reach = [_probe(grid, r) in reach for r in rooms]
    orphan_reach = [_probe(grid, r) in reach for r in orphans]
    guardian_ok = boss_room is not None and _probe(grid, boss_room) in reach

    # -- 3. mandatory rooms ----------------------------------------------------
    present = set(types)
    if RoomType.ENTRANCE not in present:
        add(FATAL, "MISSING_ENTRANCE", "no ENTRANCE room was placed")
    if RoomType.GATE not in present:
        add(ERROR, "MISSING_GATE_ROOM", "no GATE room placed; exit gates fall back to last BFS room")
    if RoomType.REWARD not in present:
        add(ERROR, "MISSING_REWARD_ROOM", "no REWARD room placed")
    if has_guardian and boss_room is None:
        add(FATAL, "MISSING_GUARDIAN", "spec expects a guardian but no GUARDIAN room was placed")
    if boss_room is not None and not guardian_ok:
        add(FATAL, "GUARDIAN_UNREACHABLE", "guardian room cannot be reached from the entrance")
    if _probe(grid, gate_room) not in reach:
        add(FATAL, "GATE_ROOM_UNREACHABLE", "gate room cannot be reached from the entrance")

    for i, (r, t) in enumerate(zip(rooms, types)):
        if room_reach[i] or r in (gate_room, boss_room):
            continue
        if t in MANDATORY_TYPES:
            add(FATAL, "MANDATORY_ROOM_UNREACHABLE", f"{t} room #{i} unreachable", room=i)
        else:
            add(ERROR, "ROOM_UNREACHABLE", f"{t} room #{i} unreachable (content wasted)", room=i)

    # -- 4. disconnected floor -------------------------------------------------
    unreached = [(c, r) for r in range(rows) for c in range(cols)
                 if grid[r][c] == 0 and (c, r) not in reach]
    m.update(reachable_floor=len(reach), unreachable_floor=len(unreached))
    orphan_islands = 0
    for comp in _components(unreached):
        kinds = {owner.get(t, ("corridor",))[0] for t in comp}
        if "room" in kinds:
            continue                      # already reported per room
        if "orphan" in kinds:
            orphan_islands += 1
        else:
            add(ERROR, "DISCONNECTED_CORRIDOR",
                f"{len(comp)} corridor tiles unreachable from entrance",
                size=len(comp), at=list(comp[0]))
    if orphan_islands:
        add(WARN, "ORPHAN_ROOM_ISLANDS",
            f"{orphan_islands} carved room(s) never connected "
            f"(dead floor that inflates the exploration denominator)", count=orphan_islands)

    # -- 5. corridors, physical room graph, dead ends -----------------------------
    corridor = [t for t in reach if t not in owner]
    adj = defaultdict(set)
    stub_corridors = 0
    for comp in _components(corridor):
        touched = set()
        for c, r in comp:
            for dc, dr in N4:
                o = owner.get((c + dc, r + dr))
                if o:
                    touched.add(o)
        if len(touched) <= 1:
            stub_corridors += 1
        for a in touched:
            adj[a] |= (touched - {a})
    if stub_corridors:
        add(WARN, "DEAD_END_CORRIDOR", f"{stub_corridors} corridor stub(s) lead nowhere")

    pointless = [i for i, t in enumerate(types)
                 if room_reach[i] and t in POINTLESS_LEAF_TYPES and len(adj[("room", i)]) <= 1]
    empty_chambers = sum(1 for ok in orphan_reach if ok)
    m.update(pointless_dead_ends=len(pointless), empty_chambers=empty_chambers)
    if empty_chambers:
        add(WARN, "EMPTY_CHAMBER",
            f"{empty_chambers} reachable room(s) not in the room list: never populated")
    if len(pointless) + empty_chambers > cfg.max_pointless_dead_ends:
        add(ERROR, "POINTLESS_DEAD_ENDS",
            f"{len(pointless)} empty leaf rooms + {empty_chambers} empty chambers "
            f"> {cfg.max_pointless_dead_ends}", rooms=pointless)

    # -- 6. exit gates -----------------------------------------------------------
    gates = simulate_exit_gates(grid, gate_room, area_level, size_tier, seal_rng_factory, ext_id)
    m["exit_gates"] = len(gates)
    if not gates:
        add(FATAL, "NO_EXIT_GATE", "no exit gate could be placed")
    for tile, n in Counter((g.col, g.row) for g in gates).items():
        if n > 1:
            add(ERROR, "GATE_DUPLICATE_TILE", f"{n} gates stacked on tile {tile}", tile=list(tile))

    for g in gates:
        in_bounds = 0 <= g.row < rows and 0 <= g.col < cols
        orig = grid[g.row][g.col] if in_bounds else 1
        if orig == 0:
            add(WARN, "GATE_ON_FLOOR",
                f"{g.label}: door on a walkable tile; DOOR_CLOSED will block passage")
        dc, dr = INWARD.get(g.side, (0, 0))
        inward = (g.col + dc, g.row + dr)
        approach = inward if inward in reach else None
        if approach is None:
            nbs = [(g.col + a, g.row + b) for a, b in N4 if (g.col + a, g.row + b) in reach]
            if nbs:
                approach = nbs[0]
                add(WARN, "GATE_SIDE_MISMATCH",
                    f"{g.label}: labelled '{g.side}' but the room is on another side")
        g.approach = approach
        if approach is None:
            add(ERROR, "GATE_INACCESSIBLE", f"{g.label}: no reachable floor next to the door")
            continue
        if not _in_rect(approach, gate_room):
            add(WARN, "GATE_OUTSIDE_GATE_ROOM", f"{g.label}: door is approached from outside the gate room")
        
        #if g.side not in ATTACHABLE_SIDES:
        #    add(ERROR, "GATE_SIDE_UNSUPPORTED",
        #        f"{g.label}: attach_extension only supports e/s and will treat this as 'e' "
        #        f"(placement line check fails, corridor carved east along row {g.row})")
        #else:
        #    odc, odr = OUTWARD[g.side]
        #    c, r = g.col + odc, g.row + odr
        #    crossed = 0
        #    while 0 <= c < cols and 0 <= r < rows:
        #        crossed += grid[r][c] == 0
        #        c += odc
        #        r += odr
        #    if crossed:
        #        add(WARN, "ATTACH_TRENCH",
        #            f"{g.label}: outward line hits {crossed} floor tiles; attach_extension "
        #            f"will carve a trench through the region", crossed=crossed)
        odc, odr = OUTWARD.get(g.side, (1, 0))
        oc, orr = g.col + odc, g.row + odr
        if 0 <= orr < rows and 0 <= oc < cols:
            if grid[orr][oc] != 1:
                add(ERROR, "GATE_NO_OUTSIDE",
                    f"{g.label}: tile beyond the door isn't wall; next corridor would open into this region")
        elif g.side in ("w", "n"):
            add(ERROR, "GATE_NO_OUTSIDE", f"{g.label}: door sits on the map edge")

    accessible = [g for g in gates if g.approach is not None]
    if gates and not accessible:
        add(FATAL, "GATE_INACCESSIBLE_ALL", "no exit gate is reachable")

    # -- 7. entrance -> exit distance ------------------------------------------------
    if accessible:
        best = min(accessible, key=lambda g: reach[g.approach])
        path = reach[best.approach]
        manh = abs(best.approach[0] - start[0]) + abs(best.approach[1] - start[1])
        span = cols + rows
        tort = path / max(1, manh)
        m.update(path_tiles=path, path_factor=round(path / span, 3),
                 tortuosity=round(tort, 2),
                 walk_seconds=round(path * ts / config.PLAYER_SPEED, 1),
                 farthest_gate_tiles=max(reach[g.approach] for g in accessible))
        if path > cfg.max_path_factor * span:
            add(ERROR, "EXIT_TOO_FAR",
                f"entrance->exit path {path} tiles > {cfg.max_path_factor}x(cols+rows)={int(cfg.max_path_factor * span)}")
        elif path < cfg.min_path_factor * span:
            add(WARN, "EXIT_TOO_CLOSE", f"entrance->exit only {path} tiles")
        if tort > cfg.max_tortuosity:
            add(WARN, "PATH_TORTUOUS", f"path is {tort:.1f}x the straight-line distance")

        # guardian should be a chokepoint between entrance and exit
        if guardian_ok:
            blocked = frozenset(_rect_tiles(boss_room, rows, cols))
            reach_nb = _bfs(grid, [start], blocked)
            if any(g.approach in reach_nb for g in accessible):
                add(WARN, "GUARDIAN_BYPASS", "exit reachable without passing the guardian room")

    # -- 8. seal objectives --------------------------------------------------------
    x0 = min(r[0] for r in rooms)
    y0 = min(r[1] for r in rooms)
    x1 = max(r[2] for r in rooms)
    y1 = max(r[3] for r in rooms)
    floor_total = floor_reach = 0
    for r in range(max(0, y0), min(rows, y1)):
        for c in range(max(0, x0), min(cols, x1)):
            if grid[r][c] == 0:
                floor_total += 1
                floor_reach += (c, r) in reach
    explore_frac = floor_reach / floor_total if floor_total else 0.0
    m["max_exploration"] = round(explore_frac, 3)

    room_enemies = [estimate_room_enemies(r, t, area_level) if room_reach[i] else (0, 0)
                    for i, (r, t) in enumerate(zip(rooms, types))]
    m["est_enemies"] = sum(c for c, _ in room_enemies)

    ec = cfg.elite_chance
    if ec is None:
        try:
            ec = float(_D.get("elite_chance")) if _D is not None else 0.12
        except Exception:
            ec = 0.12

    ctx = _Ctx(grid, reach, rooms, types, room_reach, room_enemies,
               guardian_ok, ec, explore_frac, cfg.min_arena_tiles)
    viable = 0
    for g in accessible:
        g.status, g.detail = _check_seal(g, ctx)
        if g.status in ("ok", "risky"):
            viable += 1
        if g.status == "risky":
            add(WARN, "SEAL_RISKY", f"{g.label}: {g.detail}")
        elif g.status == "unlikely":
            add(ERROR, "SEAL_UNLIKELY", f"{g.label}: {g.detail}")
        elif g.status == "impossible":
            add(ERROR, "SEAL_IMPOSSIBLE", f"{g.label}: {g.detail}")
    if gates and accessible and viable == 0:
        add(FATAL, "SOFTLOCK", "no exit gate is both reachable and completable")

    rep.gates = [g.to_dict() for g in gates]
    rep.finalize(cfg)
    if cfg.render == "always" or (cfg.render == "rejected" and not rep.accepted):
        rep.ascii = _render(grid, reach, owner, rooms, types, gates)
    return rep


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------
@contextmanager
def _capture_room_rects():
    """build_procedural_area discards the extra rects it asked for; capture them."""
    captured = {"rects": []}
    original = mapgen.generate_dungeon_grid

    def wrapper(*a, **kw):
        grid, rooms = original(*a, **kw)
        captured["rects"] = list(rooms)
        return grid, rooms

    mapgen.generate_dungeon_grid = wrapper
    try:
        yield captured
    finally:
        mapgen.generate_dungeon_grid = original


def build_area_with_capture(stream, **kwargs):
    with _capture_room_rects() as cap:
        area = mapgen.build_procedural_area(stream, **kwargs)
    return area, (cap["rects"] or list(area.get("rooms") or []))


def sample_params(rng, overrides=None, no_guardian_rate=0.0) -> dict:
    o = overrides or {}
    lvl_roll = rng.choice((rng.randint(1, 10), rng.randint(1, 30), rng.randint(10, 80)))
    st_roll = rng.choices([-1, 0, 1, 2], weights=[1, 4, 3, 2])[0]
    p = {
        "biome": o.get("biome") or rng.choice(BIOMES),
        "area_level": o.get("area_level") or lvl_roll,
        "size_tier": o["size_tier"] if o.get("size_tier") is not None else st_roll,
        "room_count": rng.randint(5, 7),
        "has_guardian": rng.random() >= no_guardian_rate,
        "archetype": o.get("archetype"),
        "cols": o.get("cols", 50),
        "rows": o.get("rows", 38),
    }
    p["entering_seal"] = o.get("entering_seal") or roll_seal_type(rng, area_level=p["area_level"])
    return p


def run_one(seed: int, cfg: Optional[BotConfig] = None, overrides=None,
            no_guardian_rate=0.0) -> MapReport:
    cfg = cfg or BotConfig()
    p = sample_params(random.Random(_derive_seed(seed, "params")), overrides, no_guardian_rate)
    stream = random.Random(_derive_seed(seed, "area"))
    try:
        kwargs = dict(area_level=p["area_level"], cols=p["cols"], rows=p["rows"],
                      room_count=p["room_count"], boss_room=p["has_guardian"],
                      extra_rooms=(p["size_tier"] if p["size_tier"] >= 0 else None),
                      biome=p["biome"], seal_type=p["entering_seal"])
        if p["archetype"]:
            graph, _, _, _ = build_room_graph(
                stream, biome=p["biome"], seal_type=p["entering_seal"],
                has_guardian=p["has_guardian"],
                force_archetype=LayoutArchetype(p["archetype"]))
            kwargs["room_graph"] = graph
        area, rects = build_area_with_capture(stream, **kwargs)
    except Exception as e:
        rep = MapReport(seed=seed, params=p)
        rep.add(FATAL, "GENERATOR_CRASH", f"{type(e).__name__}: {e}",
                traceback=traceback.format_exc())
        return rep.finalize(cfg)

    factory = lambda name: random.Random(_derive_seed(seed, name))  # noqa: E731
    try:
        return validate_area(area, all_rects=rects, area_level=p["area_level"],
                             size_tier=p["size_tier"], has_guardian=p["has_guardian"],
                             seal_rng_factory=factory, ext_id=f"bot_{seed}",
                             cfg=cfg, params=p, seed=seed)
    except Exception as e:
        rep = MapReport(seed=seed, params=p, archetype=str(area.get("archetype", "?")))
        rep.add(FATAL, "BOT_CRASH", f"{type(e).__name__}: {e}", traceback=traceback.format_exc())
        return rep.finalize(cfg)


def build_validated_area(rng_service, spec, extension_id, seal_type=None,
                         max_attempts=6, cfg: Optional[BotConfig] = None, verbose=True):
    """
    Runtime hook for Extension.ensure_built: regenerate until the bot accepts.
    Attempt 0 uses the original stream name, so existing saves rebuild identically
    whenever their map was already valid. Fully deterministic per world seed.
    """
    cfg = cfg or BotConfig(render="never")
    has_guardian = spec.boss_template_id is not None
    size_tier = getattr(spec, "size_tier", -1)
    best_rep, best_area = None, None
    for attempt in range(max_attempts):
        name = f"world:extension:{extension_id}:area" + (f":retry{attempt}" if attempt else "")
        stream = rng_service.fresh_stream(name)
        area, rects = build_area_with_capture(
            stream, area_level=spec.area_level, cols=spec.map_cols, rows=spec.map_rows,
            room_count=spec.room_count, boss_room=has_guardian,
            extra_rooms=(size_tier if size_tier >= 0 else None),
            biome=spec.biome, seal_type=seal_type)
        rep = validate_area(area, all_rects=rects, area_level=spec.area_level,
                            size_tier=size_tier, has_guardian=has_guardian,
                            seal_rng_factory=rng_service.fresh_stream,  # == World's gate_seal streams
                            ext_id=extension_id, cfg=cfg)
        area["validation"] = {"attempt": attempt, "accepted": rep.accepted,
                              "codes": [c for _, c in rep.codes()]}
        if rep.accepted:
            if verbose and attempt:
                print(f"[map_bot] {extension_id}: accepted on attempt {attempt + 1}")
            return area
        if verbose:
            print(f"[map_bot] {extension_id}: attempt {attempt + 1} rejected: "
                  f"{[c for _, c in rep.codes()]}")
        if best_rep is None or rep.score() < best_rep.score():
            best_rep, best_area = rep, area
    if verbose:
        print(f"[map_bot] {extension_id}: no clean map in {max_attempts} attempts; using least-bad")
    return best_area


# ---------------------------------------------------------------------------
# batch runner / reporting
# ---------------------------------------------------------------------------
def _worker(job):
    seed, cfg, overrides, ngr = job
    return run_one(seed, cfg, overrides, ngr).to_dict()


def run_batch(count, base_seed, cfg, overrides=None, workers=1, no_guardian_rate=0.0,
              progress=True):
    seeds = [_derive_seed(base_seed, f"map:{i}") for i in range(count)]
    jobs = [(s, cfg, overrides, no_guardian_rate) for s in seeds]
    results = []
    t0 = time.time()

    def tick(i):
        if progress and (i + 1) % 250 == 0:
            rej = sum(not r["accepted"] for r in results)
            print(f"\r  {i + 1}/{count} maps  rejected {rej} "
                  f"({rej / len(results):.1%})", end="", flush=True)

    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for i, r in enumerate(ex.map(_worker, jobs, chunksize=32)):
                results.append(r)
                tick(i)
    else:
        for i, job in enumerate(jobs):
            results.append(_worker(job))
            tick(i)
    if progress:
        print()
    return results, time.time() - t0


def _pct(xs, q):
    if not xs:
        return 0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1)))]


def summarize(results, elapsed) -> dict:
    n = len(results)
    acc = sum(r["accepted"] for r in results)
    code_maps = Counter()
    arch = defaultdict(lambda: [0, 0])
    biome = defaultdict(lambda: [0, 0])
    seal_fail = Counter()
    paths, walks = [], []
    for r in results:
        seen = set()
        for i in r["issues"]:
            key = (i["severity"], i["code"])
            if key not in seen:
                seen.add(key)
                code_maps[key] += 1
        for g in r.get("gates", []):
            if g["status"] in ("impossible", "unlikely"):
                seal_fail[g["seal"]] += 1
        a = arch[r["archetype"]]
        a[0] += 1
        a[1] += not r["accepted"]
        b = biome[r["params"].get("biome", "?")]
        b[0] += 1
        b[1] += not r["accepted"]
        if r["metrics"].get("path_tiles") is not None:
            paths.append(r["metrics"]["path_tiles"])
            walks.append(r["metrics"].get("walk_seconds", 0))
    return {
        "maps": n, "accepted": acc, "rejected": n - acc,
        "reject_rate": (n - acc) / n if n else 0.0,
        "elapsed_s": round(elapsed, 2), "maps_per_s": round(n / elapsed, 1) if elapsed else 0,
        "issues": [{"severity": s, "code": c, "maps": k, "rate": k / n}
                   for (s, c), k in sorted(code_maps.items(),
                                           key=lambda kv: (_SEV_ORDER[kv[0][0]], -kv[1]))],
        "by_archetype": {k: {"maps": v[0], "rejected": v[1], "rate": v[1] / v[0]} for k, v in arch.items()},
        "by_biome": {k: {"maps": v[0], "rejected": v[1], "rate": v[1] / v[0]} for k, v in biome.items()},
        "uncompletable_seals": dict(seal_fail.most_common()),
        "path_tiles": {"mean": round(sum(paths) / len(paths), 1) if paths else 0,
                       "p50": _pct(paths, 0.5), "p95": _pct(paths, 0.95), "max": max(paths, default=0)},
        "walk_seconds": {"p50": _pct(walks, 0.5), "p95": _pct(walks, 0.95)},
    }


def print_summary(s):
    print(f"\n=== Map bot: {s['maps']} maps in {s['elapsed_s']}s ({s['maps_per_s']} maps/s) ===")
    print(f"Accepted {s['accepted']}  |  Rejected {s['rejected']}  ({s['reject_rate']:.1%})\n")
    print("Issues (maps affected):")
    for i in s["issues"]:
        print(f"  {i['severity']:<5} {i['code']:<28} {i['maps']:>6}  {i['rate']:6.1%}")
    print("\nRejection by archetype:")
    for k, v in sorted(s["by_archetype"].items(), key=lambda kv: -kv[1]["rate"]):
        print(f"  {k:<14} {v['rejected']:>5}/{v['maps']:<6} {v['rate']:6.1%}")
    print("\nRejection by biome:")
    for k, v in sorted(s["by_biome"].items(), key=lambda kv: -kv[1]["rate"]):
        print(f"  {k:<18} {v['rejected']:>5}/{v['maps']:<6} {v['rate']:6.1%}")
    if s["uncompletable_seals"]:
        print("\nUncompletable gate seals (impossible/unlikely):")
        for k, v in s["uncompletable_seals"].items():
            print(f"  {k:<18} {v}")
    pt, ws = s["path_tiles"], s["walk_seconds"]
    print(f"\nEntrance->exit path: mean {pt['mean']}  p50 {pt['p50']}  p95 {pt['p95']}  "
          f"max {pt['max']} tiles  (walk p50 {ws['p50']}s / p95 {ws['p95']}s)")


def format_report(r: dict) -> str:
    lines = [f"seed={r['seed']}  archetype={r['archetype']}  accepted={r['accepted']}",
             "params:  " + json.dumps(r["params"], default=str),
             "metrics: " + json.dumps(r["metrics"], default=str), "gates:"]
    for g in r.get("gates", []):
        lines.append(f"  #{g['index'] + 1} {g['tier']:<9} side={g['side']} tile={g['tile']} "
                     f"seal={g['seal']}({g['target']}) -> {g['status']} {g['detail']}")
    lines.append("issues:")
    for i in r["issues"]:
        lines.append(f"  [{i['severity']}] {i['code']}: {i['message']}")
    if r.get("ascii"):
        lines += ["", r["ascii"]]
    return "\n".join(lines)


def dump_failures(results, out_dir, limit=60, per_code=5):
    os.makedirs(out_dir, exist_ok=True)
    per = Counter()
    written = 0
    for r in results:
        if r["accepted"] or written >= limit or not r["issues"]:
            continue
        code = r["issues"][0]["code"]
        if per[code] >= per_code:
            continue
        per[code] += 1
        written += 1
        with open(os.path.join(out_dir, f"{code}_{r['seed']}.txt"), "w", encoding="utf-8") as f:
            f.write(format_report(r))
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description="Level 1 map-testing bot")
    ap.add_argument("--count", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--report", help="write JSON report here")
    ap.add_argument("--report-all", action="store_true", help="include accepted maps in JSON")
    ap.add_argument("--dump-dir", help="write ASCII renders of rejected maps here")
    ap.add_argument("--dump-limit", type=int, default=60)
    ap.add_argument("--accepted-out", help="write accepted map seeds (one per line)")
    ap.add_argument("--replay", type=int, help="regenerate and print a single map seed")
    ap.add_argument("--max-reject-rate", type=float, help="exit 1 if rejection rate exceeds this (CI)")
    ap.add_argument("--strict", action="store_true", help="reject on any warning")
    ap.add_argument("--lenient", action="store_true", help="only FATAL rejects")
    ap.add_argument("--level", type=int)
    ap.add_argument("--biome")
    ap.add_argument("--archetype", choices=[a.value for a in LayoutArchetype])
    ap.add_argument("--size-tier", type=int, choices=[-1, 0, 1, 2])
    ap.add_argument("--no-guardian-rate", type=float, default=0.0,
                    help="fraction of specs with boss_template_id=None (template extensions)")
    ap.add_argument("--max-path-factor", type=float, default=BotConfig.max_path_factor)
    ap.add_argument("--max-dead-ends", type=int, default=BotConfig.max_pointless_dead_ends)
    args = ap.parse_args(argv)

    cfg = BotConfig(max_path_factor=args.max_path_factor,
                    max_pointless_dead_ends=args.max_dead_ends,
                    max_warnings=0 if args.strict else BotConfig.max_warnings,
                    reject_on_error=not args.lenient)
    overrides = {"area_level": args.level, "biome": args.biome,
                 "archetype": args.archetype, "size_tier": args.size_tier}

    if args.replay is not None:
        cfg.render = "always"
        rep = run_one(args.replay, cfg, overrides, args.no_guardian_rate)
        print(format_report(rep.to_dict()))
        print("\n(replay uses the same --level/--biome/--archetype/--size-tier/"
              "--no-guardian-rate as the original run)")
        return 0 if rep.accepted else 2

    results, elapsed = run_batch(args.count, args.seed, cfg, overrides,
                                 args.workers, args.no_guardian_rate)
    summary = summarize(results, elapsed)
    print_summary(summary)

    if args.report:
        maps = results if args.report_all else [r for r in results if not r["accepted"]]
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "maps": maps}, f, indent=1, default=str)
        print(f"\nJSON report -> {args.report}")
    if args.dump_dir:
        n = dump_failures(results, args.dump_dir, args.dump_limit)
        print(f"{n} failure renders -> {args.dump_dir}/")
    if args.accepted_out:
        with open(args.accepted_out, "w") as f:
            f.writelines(f"{r['seed']}\n" for r in results if r["accepted"])
        print(f"accepted seeds -> {args.accepted_out}")

    if args.max_reject_rate is not None and summary["reject_rate"] > args.max_reject_rate:
        print(f"\nFAIL: reject rate {summary['reject_rate']:.1%} > {args.max_reject_rate:.1%}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
