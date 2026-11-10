"""Room graphs for procedural dungeon generation.

Phase 1: room types + layout archetypes. The graph is generated first,
then the physical grid is laid out to match the graph (see
world/map_generator.py:build_procedural_area).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class RoomType(str, Enum):
    ENTRANCE  = "entrance"
    COMBAT    = "combat"
    ELITE     = "elite"
    TREASURE  = "treasure"
    SHRINE    = "shrine"
    EVENT     = "event"
    HAZARD    = "hazard"
    CHALLENGE = "challenge"
    SECRET    = "secret"
    GUARDIAN  = "guardian"
    GATE      = "gate"
    REWARD    = "reward"

    def __str__(self):
        return self.value


class LayoutArchetype(str, Enum):
    BRANCH_MERGE = "branch_merge"
    HUB_SPOKES   = "hub_spokes"
    LOOP         = "loop"
    GAUNTLET     = "gauntlet"
    LABYRINTH    = "labyrinth"

    def __str__(self):
        return self.value


@dataclass
class RoomNode:
    index: int
    room_type: RoomType = RoomType.COMBAT
    depth: int = 0
    branch: int = 0
    tags: Dict[str, object] = field(default_factory=dict)

    def is_special(self) -> bool:
        return self.room_type in (
            RoomType.ENTRANCE, RoomType.GUARDIAN,
            RoomType.GATE, RoomType.REWARD,
        )


@dataclass
class RoomGraph:
    archetype: LayoutArchetype
    nodes: List[RoomNode] = field(default_factory=list)
    edges: List[Tuple[int, int]] = field(default_factory=list)

    def add_node(self, room_type=RoomType.COMBAT, depth=0, branch=0, **tags) -> int:
        idx = len(self.nodes)
        self.nodes.append(RoomNode(idx, room_type, depth, branch, tags))
        return idx

    def connect(self, a: int, b: int):
        if a == b:
            return
        edge = (min(a, b), max(a, b))
        if edge not in self.edges:
            self.edges.append(edge)

    def node(self, idx: int) -> RoomNode:
        return self.nodes[idx]

    def neighbors(self, idx: int) -> List[int]:
        out = []
        for a, b in self.edges:
            if a == idx:
                out.append(b)
            elif b == idx:
                out.append(a)
        return out

    def nodes_of_type(self, room_type: RoomType) -> List[RoomNode]:
        return [n for n in self.nodes if n.room_type == room_type]

    def has_cycle(self) -> bool:
        parent = list(range(len(self.nodes)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in self.edges:
            ra, rb = find(a), find(b)
            if ra == rb:
                return True
            parent[ra] = rb
        return False

    def is_connected_from(self, root: int) -> bool:
        seen = {root}
        stack = [root]
        while stack:
            cur = stack.pop()
            for nb in self.neighbors(cur):
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        return len(seen) == len(self.nodes)


def bfs_order(g: RoomGraph, root: int) -> List[int]:
    seen = [root]
    seen_set = {root}
    queue = [root]
    while queue:
        cur = queue.pop(0)
        for nb in g.neighbors(cur):
            if nb not in seen_set:
                seen_set.add(nb)
                seen.append(nb)
                queue.append(nb)
    return seen


# ---------------------------------------------------------------------------
# Type assignment
# ---------------------------------------------------------------------------
_FIXED_SPECIAL = {RoomType.ENTRANCE, RoomType.GUARDIAN,
                  RoomType.GATE, RoomType.REWARD}

BASE_TYPE_WEIGHTS: Dict[RoomType, float] = {
    RoomType.COMBAT:    5.0,
    RoomType.ELITE:     1.2,
    RoomType.TREASURE:  1.0,
    RoomType.SHRINE:    1.0,
    RoomType.EVENT:     0.8,
    RoomType.HAZARD:    0.8,
    RoomType.CHALLENGE: 0.6,
    RoomType.SECRET:    0.35,
}

SEAL_ROOM_BIAS: Dict[str, Dict[RoomType, float]] = {
    "boss":         {RoomType.ELITE: 2.0, RoomType.REWARD: 1.5},
    "shrines":      {RoomType.SHRINE: 4.0},
    "elite_hunt":   {RoomType.ELITE: 4.0},
    "purge":        {RoomType.COMBAT: 4.0},
    "survival":     {RoomType.CHALLENGE: 5.0},
    "trial":        {RoomType.CHALLENGE: 3.0, RoomType.COMBAT: 2.0},
    "bargain":      {RoomType.TREASURE: 3.5},
    "exploration":  {RoomType.SECRET: 4.0},
    "hunt_nemesis": {RoomType.COMBAT: 1.5},
    "corruption":   {RoomType.HAZARD: 4.0},
}


def _weighted_choice(rng, weights: Dict):
    items = [(k, w) for k, w in weights.items() if w > 0]
    total = sum(w for _, w in items)
    if total <= 0:
        return next(iter(weights))
    r = rng.uniform(0, total)
    acc = 0.0
    for k, w in items:
        acc += w
        if r <= acc:
            return k
    return items[-1][0]


def _assign_room_types(g: RoomGraph, rng, seal_type: Optional[str] = None):
    bias = dict(SEAL_ROOM_BIAS.get(seal_type or "", {}))
    for n in g.nodes:
        if n.room_type in _FIXED_SPECIAL:
            continue
        if n.tags.get("preserve"):
            continue
        if n.tags.get("role") in ("hub", "crossroads"):
            n.room_type = RoomType.EVENT
            continue
        weights = dict(BASE_TYPE_WEIGHTS)
        for k, v in bias.items():
            weights[k] = weights.get(k, 0.0) + v
        n.room_type = _weighted_choice(rng, weights)


# ---------------------------------------------------------------------------
# Archetype selection
# ---------------------------------------------------------------------------
BIOME_ARCHETYPE_WEIGHTS: Dict[str, Dict[LayoutArchetype, float]] = {
    "ash_canyon":       {LayoutArchetype.GAUNTLET: 3, LayoutArchetype.LOOP: 2,
                         LayoutArchetype.BRANCH_MERGE: 2},
    "sunken_library":   {LayoutArchetype.LABYRINTH: 3, LayoutArchetype.HUB_SPOKES: 2,
                         LayoutArchetype.BRANCH_MERGE: 2},
    "corrupted_hollow": {LayoutArchetype.BRANCH_MERGE: 3, LayoutArchetype.LABYRINTH: 2,
                         LayoutArchetype.LOOP: 1},
    "frozen_reach":     {LayoutArchetype.LOOP: 3, LayoutArchetype.LABYRINTH: 2,
                         LayoutArchetype.GAUNTLET: 1},
    "storm_peaks":      {LayoutArchetype.BRANCH_MERGE: 3, LayoutArchetype.GAUNTLET: 2,
                         LayoutArchetype.HUB_SPOKES: 1},
    "bone_wastes":      {LayoutArchetype.GAUNTLET: 3, LayoutArchetype.LABYRINTH: 2,
                         LayoutArchetype.BRANCH_MERGE: 1},
    "verdant_ruin":     {LayoutArchetype.HUB_SPOKES: 3, LayoutArchetype.BRANCH_MERGE: 2,
                         LayoutArchetype.LOOP: 1},
}

DEFAULT_ARCHETYPE_WEIGHTS: Dict[LayoutArchetype, float] = {
    LayoutArchetype.BRANCH_MERGE: 3,
    LayoutArchetype.LOOP: 2,
    LayoutArchetype.HUB_SPOKES: 2,
    LayoutArchetype.GAUNTLET: 2,
    LayoutArchetype.LABYRINTH: 1,
}


def pick_archetype(rng, biome: Optional[str] = None) -> LayoutArchetype:
    weights = BIOME_ARCHETYPE_WEIGHTS.get((biome or "").lower(),
                                          DEFAULT_ARCHETYPE_WEIGHTS)
    return _weighted_choice(rng, weights)


# ---------------------------------------------------------------------------
# Topology builders
# ---------------------------------------------------------------------------
def _build_branch_merge(rng):
    g = RoomGraph(LayoutArchetype.BRANCH_MERGE)
    entrance   = g.add_node(RoomType.ENTRANCE, depth=0, branch=0)
    crossroads = g.add_node(RoomType.EVENT,    depth=1, branch=0, role="crossroads")
    g.connect(entrance, crossroads)

    n_branches = rng.randint(2, 3)
    ends = []
    for b in range(n_branches):
        length = rng.randint(1, 2)
        prev = crossroads
        d = 1
        for _ in range(length):
            d += 1
            n = g.add_node(RoomType.COMBAT, depth=d, branch=b + 1)
            g.connect(prev, n)
            prev = n
        ends.append(prev)

    merge = g.add_node(RoomType.COMBAT, depth=3, branch=0)
    for end in ends:
        g.connect(end, merge)

    guardian = g.add_node(RoomType.GUARDIAN, depth=4, branch=0)
    reward   = g.add_node(RoomType.REWARD,   depth=5, branch=0)
    gate     = g.add_node(RoomType.GATE,     depth=6, branch=0)
    g.connect(merge, guardian)
    g.connect(guardian, reward)
    g.connect(reward, gate)
    return g, entrance, guardian, gate


def _build_hub_spokes(rng):
    g = RoomGraph(LayoutArchetype.HUB_SPOKES)
    entrance = g.add_node(RoomType.ENTRANCE, depth=0)
    hub      = g.add_node(RoomType.EVENT,    depth=1, branch=0, role="hub")
    g.connect(entrance, hub)

    for b in range(rng.randint(3, 4)):
        length = rng.randint(1, 2)
        prev = hub
        for i in range(length):
            n = g.add_node(RoomType.COMBAT, depth=2 + i, branch=b + 1)
            g.connect(prev, n)
            prev = n

    guardian = g.add_node(RoomType.GUARDIAN, depth=4, branch=0)
    reward   = g.add_node(RoomType.REWARD,   depth=5, branch=0)
    gate     = g.add_node(RoomType.GATE,     depth=6, branch=0)
    g.connect(hub, guardian)
    g.connect(guardian, reward)
    g.connect(reward, gate)
    return g, entrance, guardian, gate


def _build_loop(rng):
    g = RoomGraph(LayoutArchetype.LOOP)
    entrance = g.add_node(RoomType.ENTRANCE, depth=0)
    chain_len = rng.randint(3, 4)
    ring = []
    prev = entrance
    for i in range(chain_len):
        n = g.add_node(RoomType.COMBAT, depth=1 + i, branch=1)
        g.connect(prev, n)
        ring.append(n)
        prev = n
    # Shortcut: last ring room connects back to the entrance -> cycle
    g.connect(ring[-1], entrance)

    guardian = g.add_node(RoomType.GUARDIAN, depth=chain_len + 1)
    reward   = g.add_node(RoomType.REWARD,   depth=chain_len + 2)
    gate     = g.add_node(RoomType.GATE,     depth=chain_len + 3)
    g.connect(ring[-1], guardian)
    g.connect(guardian, reward)
    g.connect(reward, gate)
    return g, entrance, guardian, gate


def _build_gauntlet(rng):
    g = RoomGraph(LayoutArchetype.GAUNTLET)
    entrance = g.add_node(RoomType.ENTRANCE, depth=0)
    n_rooms = rng.randint(4, 6)
    prev = entrance
    for i in range(n_rooms):
        is_elite = (i == n_rooms - 1)
        n = g.add_node(RoomType.ELITE if is_elite else RoomType.COMBAT,
                       depth=1 + i, preserve=is_elite)
        g.connect(prev, n)
        prev = n

    guardian = g.add_node(RoomType.GUARDIAN, depth=n_rooms + 1)
    reward   = g.add_node(RoomType.REWARD,   depth=n_rooms + 2)
    gate     = g.add_node(RoomType.GATE,     depth=n_rooms + 3)
    g.connect(prev, guardian)
    g.connect(guardian, reward)
    g.connect(reward, gate)
    return g, entrance, guardian, gate


def _build_labyrinth(rng):
    g = RoomGraph(LayoutArchetype.LABYRINTH)
    entrance = g.add_node(RoomType.ENTRANCE, depth=0)
    core     = g.add_node(RoomType.EVENT,    depth=1, branch=0, role="hub")
    g.connect(entrance, core)

    for b in range(rng.randint(3, 5)):
        prev = core
        length = rng.randint(1, 3)
        for i in range(length):
            n = g.add_node(RoomType.COMBAT, depth=2 + i, branch=b + 1,
                           dead_end=(i == length - 1))
            g.connect(prev, n)
            prev = n

    guardian = g.add_node(RoomType.GUARDIAN, depth=4)
    reward   = g.add_node(RoomType.REWARD,   depth=5)
    gate     = g.add_node(RoomType.GATE,     depth=6)
    g.connect(core, guardian)
    g.connect(guardian, reward)
    g.connect(reward, gate)
    return g, entrance, guardian, gate


_BUILDERS = {
    LayoutArchetype.BRANCH_MERGE: _build_branch_merge,
    LayoutArchetype.HUB_SPOKES:   _build_hub_spokes,
    LayoutArchetype.LOOP:         _build_loop,
    LayoutArchetype.GAUNTLET:     _build_gauntlet,
    LayoutArchetype.LABYRINTH:    _build_labyrinth,
}


def build_room_graph(rng, target_rooms: int = 8, biome: Optional[str] = None,
                     seal_type: Optional[str] = None, has_guardian: bool = True,
                     force_archetype: Optional[LayoutArchetype] = None):
    """Return (graph, entrance_idx, guardian_idx, gate_idx)."""
    archetype = force_archetype or pick_archetype(rng, biome)
    g, entrance, guardian, gate = _BUILDERS[archetype](rng)

    _assign_room_types(g, rng, seal_type=seal_type)

    if not has_guardian:
        for n in g.nodes:
            if n.room_type == RoomType.GUARDIAN:
                n.room_type = RoomType.COMBAT
                break
    return g, entrance, guardian, gate