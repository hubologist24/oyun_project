"""
test_helpers10.py - regression tests for the single-map expansion pass
(locked-door gates, map growth, region tracking) plus the places it
touches (combat/kill pipeline, bosses list, save/load replay,
exploration, UI draw, fast travel).

Covers:
  1.  TileMap tile semantics -- closed door is solid, open door is
      walkable, ensure_size pads with wall and never shrinks, draw is safe.
  2.  Gate.side / World.register_* -- default, round-trip, legacy dicts.
  3.  ExplorationTracker.register_region -- idempotent, min total 1.
  4.  find_door_site -- strict preference, relaxed fallback, no rooms.
  5.  stamp_door -- only closed gates stamp a door tile.
  6.  Starting area gets a locked door tile on a room wall.
  7.  Locked door blocks movement; open door does not.
  8.  attach_extension grows the map beyond the old edge and only
      modifies old tiles along the door's corridor line.
  9.  New region is connected (BFS) to the start, has its own enemies,
      boss, and a fresh closed door.
 10.  Staging / reroll / push-back do not touch the map.
 11.  Accept with no sealed door left is a safe no-op.
 12.  Second/third expansion: no region overlap, door invariants hold.
 13.  Multi-seed stress: 3 expansions per seed, all invariants.
 14.  Determinism: same seed + same actions => identical grid.
 15.  Region lookup (tile, corridor, pixel) + _update_current_region
      emits area_entered exactly once per change.
 16.  Fast travel: teleport to region / back, gateway menu entries.
 17.  Camera world size follows map growth.
 18.  Exploration registered per region; profile still builds.
 19.  Kill pipeline (starting area): XP + loot + removal (regression of
      the commented-out _on_enemy_killed call).
 20.  Kill pipeline (inside a region): loot is tagged with the region id.
 21.  Bosses list: live targets, _nearest_boss, kill event, projectile hit.
 22.  Far enemies/bosses are not updated; near ones are.
 23.  Boss melee only calls player.take_damage once (duplicate-hit fix).
 24.  Respawn goes to current region's start.
 25.  Save/load replays expansions and rebuilds an identical map.
 26.  F4 toggle + draw smoke tests in every state.
 27.  Backward-compat: Extension.ensure_built still works standalone.

Companion to test_helpers.py / 2 / 5 / 6 / 7.

Run:  python -m test.test_helpers10
"""
import os

# Must be set before pygame (or anything that imports it).
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import copy
import random
import shutil
import tempfile
import traceback
from collections import deque

import pygame

import core.config as config
from world.tilemap import TileMap, FLOOR, WALL, DOOR_CLOSED, DOOR_OPEN


# ---------------------------------------------------------------------------
# small runner harness (same shape as test_helpers5 / 6 / 7)
# ---------------------------------------------------------------------------

def _run(name, fn):
    print(f"=== {name} ===")
    try:
        result = fn()
    except Exception as e:
        result = {"errors": [f"{type(e).__name__}: {e}"],
                  "traceback": traceback.format_exc()}
    if not isinstance(result, dict):
        result = {"result": result}
    result.setdefault("errors", [])
    print(result)
    print()
    return result


def _make_game(seed=None):
    """seed makes world_seed (and therefore the whole map) reproducible."""
    if seed is not None:
        random.seed(seed)
    from core.game import Game
    return Game()


def _normalize_clock(game, playtime=100.0, last=100.0):
    game.world.total_playtime_seconds = playtime
    game.world.playtime_at_last_extension = last


def _stage_and_accept(game, mode=None):
    """Stage an offer, remember its spec/gate, accept it."""
    mode = mode or config.EXTENSION_MODE_MANUAL
    _normalize_clock(game)
    game._generate_next_extension(mode=mode)
    spec = game._pending_extension_spec
    gate = game._pending_gate
    game._accept_pending_extension()
    return spec, gate


def _tile(x, y):
    ts = config.TILE_SIZE
    return int(x // ts), int(y // ts)


def _reachable(tm, start, goal):
    """4-neighbour BFS over non-solid tiles."""
    if tm.is_wall(*start) or tm.is_wall(*goal):
        return False
    seen = {start}
    q = deque([start])
    while q:
        c, r = q.popleft()
        if (c, r) == goal:
            return True
        for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (c + dc, r + dr)
            if n in seen or tm.is_wall(*n):
                continue
            seen.add(n)
            q.append(n)
    return False


def _enemy_in_region(game, rid):
    for e in game.current_area.enemies:
        if e.alive and getattr(e, "region_id", "starting_area") == rid:
            return e
    return None


def _check_invariants(game):
    """Structural truths that must hold after ANY number of expansions."""
    from world.level import LEVEL_SPEC
    errs = []
    area = game.current_area
    tm = area.tilemap

    # rectangular grid
    widths = {len(r) for r in tm.grid}
    if len(widths) != 1:
        errs.append(f"grid rows have different widths: {sorted(widths)}")
    if tm.rows != len(tm.grid) or tm.cols != (len(tm.grid[0]) if tm.grid else 0):
        errs.append("tilemap rows/cols out of sync with grid")

    # door tile state matches gate state
    for g in game.world.gates:
        if not (0 <= g.tile_row < tm.rows and 0 <= g.tile_col < tm.cols):
            errs.append(f"gate {g.gate_id} outside the grid")
            continue
        tile = tm.grid[g.tile_row][g.tile_col]
        want = DOOR_OPEN if g.is_open() else DOOR_CLOSED
        if tile != want:
            errs.append(f"gate {g.gate_id} state={g.state} but tile={tile} (want {want})")
        if g.side not in ("e", "s"):
            errs.append(f"gate {g.gate_id} has bad side {g.side!r}")
        if g.is_open() and g.target_extension_id is None:
            errs.append(f"gate {g.gate_id} open without target")

    # regions: inside the grid, no overlap with each other or the start map
    start_rect = pygame.Rect(0, 0, LEVEL_SPEC["cols"], LEVEL_SPEC["rows"])
    rects = area.region_rects
    for rid, rect in rects:
        if rect.right > tm.cols or rect.bottom > tm.rows:
            errs.append(f"region {rid} rect {rect} exceeds grid {tm.cols}x{tm.rows}")
        if rect.colliderect(start_rect):
            errs.append(f"region {rid} overlaps the starting map")
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            if rects[i][1].colliderect(rects[j][1]):
                errs.append(f"regions {rects[i][0]} and {rects[j][0]} overlap")

    # every region start reachable from the starting start tile
    start_tile = _tile(*area.start_pos)
    for rid, pos in area.region_start.items():
        if not _reachable(tm, start_tile, _tile(*pos)):
            errs.append(f"region {rid} start not reachable from spawn")

    # everything that spawned stands on a walkable tile
    for e in area.enemies:
        if tm.is_wall(*_tile(e.x, e.y)):
            errs.append(f"enemy {e.name} spawned inside a wall")
    for b in area.bosses:
        if tm.is_wall(*_tile(b.x, b.y)):
            errs.append(f"boss {b.name} spawned inside a wall")
    return errs


# ---------------------------------------------------------------------------
# 1. TileMap tile semantics
# ---------------------------------------------------------------------------

def test_tilemap_tile_semantics():
    ts = config.TILE_SIZE
    grid = [[1, 1, 1],
            [1, 0, 2],
            [1, 3, 1]]
    tm = TileMap(grid)

    def rect_at(col, row):
        return pygame.Rect(col * ts + 4, row * ts + 4, ts - 8, ts - 8)

    # ensure_size: grows, pads with wall, preserves, never shrinks
    tm.ensure_size(5, 5)
    grew = (tm.cols == 5 and tm.rows == 5
            and all(len(r) == 5 for r in tm.grid))
    padded = (tm.grid[0][4] == WALL and tm.grid[4][0] == WALL
              and tm.grid[4][4] == WALL)
    preserved = tm.grid[1][2] == DOOR_CLOSED and tm.grid[2][1] == DOOR_OPEN
    tm.ensure_size(2, 2)
    not_shrunk = tm.cols == 5 and tm.rows == 5

    # draw smoke over every tile type
    try:
        from world.camera import Camera
        cam = Camera(*tm.pixel_size())
        cam.update(0, 0)
        surf = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        tm.draw(surf, cam)
        drew = True
    except Exception as e:
        drew = f"{type(e).__name__}: {e}"

    return {
        "closed_door_is_solid": tm.collides_rect(rect_at(2, 1)) is True,
        "open_door_is_walkable": tm.collides_rect(rect_at(1, 2)) is False,
        "floor_is_walkable": tm.collides_rect(rect_at(1, 1)) is False,
        "wall_is_solid": tm.collides_rect(rect_at(0, 0)) is True,
        "out_of_bounds_is_wall": tm.is_wall(-1, 0) and tm.is_wall(99, 99),
        "ensure_size_grows": grew,
        "ensure_size_pads_with_wall": padded,
        "ensure_size_preserves_tiles": preserved,
        "ensure_size_never_shrinks": not_shrunk,
        "draw_all_tile_types_ok": drew is True,
        "errors": [] if drew is True else [str(drew)],
    }


# ---------------------------------------------------------------------------
# 2. Gate.side + World.register_* signatures
# ---------------------------------------------------------------------------

def test_gate_side_roundtrip_and_defaults():
    from world.gate import Gate
    from world.world import World

    g = Gate(gate_id=1, owner_area_id="a", room_index=0, tile_col=3, tile_row=4)
    d = g.to_dict()

    legacy = {k: v for k, v in d.items() if k != "side"}
    legacy_gate = Gate.from_dict(legacy)

    g_s = Gate(gate_id=2, owner_area_id="a", room_index=0, tile_col=1,
               tile_row=1, side="s")
    g_s_back = Gate.from_dict(g_s.to_dict())

    w = World(5)
    sg = w.register_starting_gate(0, 1, 1, "s")
    eg_default = w.register_extension_gate("ext_a", 0, 2, 2)
    eg_pos = w.register_extension_gate("ext_b", 0, 3, 3, "s")
    # pre-expansion call shape (no side) must keep working
    legacy_call = w.register_starting_gate(room_index=0, tile_col=9, tile_row=9)

    return {
        "default_side_is_e": g.side == "e",
        "to_dict_has_side": d.get("side") == "e",
        "legacy_dict_defaults_to_e": legacy_gate.side == "e",
        "side_s_roundtrips": g_s_back.side == "s",
        "starting_gate_side_stored": sg.side == "s",
        "extension_gate_default_e": eg_default.side == "e",
        "extension_gate_positional_side": eg_pos.side == "s",
        "legacy_register_call_still_works": legacy_call.side == "e",
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 3. ExplorationTracker.register_region
# ---------------------------------------------------------------------------

def test_exploration_register_region():
    from history.exploration import ExplorationTracker
    t = ExplorationTracker()

    t.register_region("r1", 10)
    t.register_region("r1", 9999)         # idempotent: ignored
    t.register_region("r0", 0)            # clamp to 1, never divide by zero

    ts = config.TILE_SIZE
    t._timer = 999
    t.update(0.0, "r1", 2 * ts + 1, 3 * ts + 1)

    return {
        "total_registered": t.area_totals.get("r1") == 10,
        "second_register_ignored": t.area_totals.get("r1") == 10,
        "zero_total_clamped_to_1": t.area_totals.get("r0") == 1,
        "fraction_after_one_sample": abs(t.fraction_for("r1") - 0.1) < 1e-9,
        "empty_region_fraction_zero": t.fraction_for("r0") == 0.0,
        "average_in_range": 0.0 <= t.average_fraction() <= 1.0,
        "roundtrip_keeps_region":
            ExplorationTracker.from_dict(t.to_dict()).area_totals.get("r1") == 10,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 4. find_door_site
# ---------------------------------------------------------------------------

def _blank(cols, rows):
    return [[WALL] * cols for _ in range(rows)]


def _carve(grid, x0, y0, x1, y1):
    for r in range(y0, y1):
        for c in range(x0, x1):
            grid[r][c] = FLOOR


def test_find_door_site():
    from world.expansion import find_door_site

    # (a) clean room: prefers south, wall tile just outside the room
    g = _blank(10, 10)
    _carve(g, 2, 2, 6, 6)
    a = find_door_site(g, [(2, 2, 6, 6)])

    # (b) south path blocked by another room -> strict pass picks east
    g = _blank(14, 14)
    _carve(g, 2, 2, 6, 6)       # room A
    _carve(g, 2, 8, 6, 12)      # room B under A
    b = find_door_site(g, [(2, 2, 6, 6)])

    # (c) both directions blocked -> relaxed pass still returns a site
    g = _blank(14, 14)
    _carve(g, 2, 2, 6, 6)
    _carve(g, 2, 8, 6, 12)
    _carve(g, 8, 2, 12, 6)      # room C to the east
    c = find_door_site(g, [(2, 2, 6, 6)])

    # (d) nothing to choose from
    d = find_door_site(_blank(6, 6), [])

    # (e) returned tile is a wall with floor right behind it
    ok_geometry = True
    for site, grid_ in ((a, None),):
        pass
    g = _blank(10, 10)
    _carve(g, 2, 2, 6, 6)
    col, row, side, idx = find_door_site(g, [(2, 2, 6, 6)])
    dx, dy = (1, 0) if side == "e" else (0, 1)
    ok_geometry = g[row][col] == WALL and g[row - dy][col - dx] == FLOOR

    return {
        "clean_room_prefers_south": a == (4, 6, "s", 0),
        "blocked_south_falls_to_east": b is not None and b[2] == "e",
        "relaxed_pass_finds_site": c == (4, 6, "s", 0),
        "no_rooms_returns_none": d is None,
        "door_is_wall_with_floor_behind": ok_geometry,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 5. stamp_door
# ---------------------------------------------------------------------------

def test_stamp_door_only_for_closed_gates():
    from world.expansion import stamp_door
    from world.gate import Gate

    class _L:  # minimal level stand-in
        pass

    lvl = _L()
    lvl.tilemap = TileMap(_blank(6, 6))

    closed = Gate(1, "x", 0, 2, 2)
    stamp_door(lvl, closed)

    opened = Gate(2, "x", 0, 4, 4)
    opened.open_to("ext")
    stamp_door(lvl, opened)

    return {
        "closed_gate_stamps_door": lvl.tilemap.grid[2][2] == DOOR_CLOSED,
        "open_gate_not_stamped": lvl.tilemap.grid[4][4] == WALL,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 6. Starting area has a locked door tile
# ---------------------------------------------------------------------------

def test_starting_area_has_locked_door():
    game = _make_game(seed=1)
    gates = game.world.gates_in_area("starting_area")
    g = gates[0] if gates else None
    tm = game.current_area.tilemap

    tile = tm.grid[g.tile_row][g.tile_col] if g else None
    dx, dy = ((1, 0) if g and g.side == "e" else (0, 1))
    behind = (tm.grid[g.tile_row - dy][g.tile_col - dx] if g else None)

    # idempotent: calling again must not add a second gate
    game._ensure_starting_gate_registered()

    return {
        "exactly_one_starting_gate": len(gates) == 1,
        "gate_is_closed": g is not None and g.state == "closed",
        "gate_flagged_starting": g is not None and g.is_starting_gate,
        "door_tile_stamped": tile == DOOR_CLOSED,
        "floor_behind_door": behind == FLOOR,
        "ensure_is_idempotent":
            len(game.world.gates_in_area("starting_area")) == 1,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 7. Locked door blocks, open door lets through
# ---------------------------------------------------------------------------

def test_locked_door_blocks_then_opens():
    game = _make_game(seed=2)
    ts = config.TILE_SIZE
    tm = game.current_area.tilemap
    g = game.world.gates_in_area("starting_area")[0]

    def player_rect_on(g_):
        cx = g_.tile_col * ts + ts / 2
        cy = g_.tile_row * ts + ts / 2
        return pygame.Rect(int(cx - 16), int(cy - 16), 32, 32)

    blocked_before = tm.collides_rect(player_rect_on(g))
    _stage_and_accept(game)
    blocked_after = tm.collides_rect(player_rect_on(g))

    return {
        "closed_door_blocks_player": blocked_before is True,
        "gate_open_after_accept": g.is_open(),
        "open_door_lets_player_through": blocked_after is False,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 8. attach_extension: map growth + minimal modification of old tiles
# ---------------------------------------------------------------------------

def test_expansion_grows_map_beyond_old_edge():
    game = _make_game(seed=3)
    tm = game.current_area.tilemap
    before_cols, before_rows = tm.cols, tm.rows
    before_grid = copy.deepcopy(tm.grid)
    n_start_enemies = len(game.current_area.enemies)

    spec, gate = _stage_and_accept(game)
    rid = spec.extension_id
    rect = dict(game.current_area.region_rects).get(rid)

    if gate.side == "e":
        beyond = rect is not None and rect.left >= before_cols
        line_ok = lambda c, r: r == gate.tile_row
    else:
        beyond = rect is not None and rect.top >= before_rows
        line_ok = lambda c, r: c == gate.tile_col

    # old tiles may only change along the door's corridor line
    stray = []
    for r in range(before_rows):
        for c in range(before_cols):
            if tm.grid[r][c] != before_grid[r][c]:
                if (c, r) == (gate.tile_col, gate.tile_row):
                    continue
                if not line_ok(c, r):
                    stray.append((c, r))

    return {
        "map_grew": tm.cols > before_cols or tm.rows > before_rows,
        "region_rect_registered": rect is not None,
        "region_placed_beyond_old_edge": beyond,
        "door_tile_now_open": tm.grid[gate.tile_row][gate.tile_col] == DOOR_OPEN,
        "door_no_longer_solid": not tm.is_wall(gate.tile_col, gate.tile_row),
        "old_tiles_only_changed_on_corridor_line": stray == [],
        "stray_tiles": stray[:5],
        "starting_enemies_untouched":
            sum(1 for e in game.current_area.enemies
                if getattr(e, "region_id", "starting_area") == "starting_area")
            == n_start_enemies,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 9. New region: connected, populated, own door
# ---------------------------------------------------------------------------

def test_new_region_connected_populated_and_has_next_door():
    game = _make_game(seed=4)
    spec, gate = _stage_and_accept(game)
    rid = spec.extension_id
    area = game.current_area
    tm = area.tilemap

    start_tile = _tile(*area.start_pos)
    region_tile = _tile(*area.region_start[rid])

    new_gates = game.world.gates_in_area(rid)
    ng = new_gates[0] if new_gates else None

    region_enemies = [e for e in area.enemies
                      if getattr(e, "region_id", None) == rid]
    region_bosses = [b for b in area.bosses
                     if getattr(b, "region_id", None) == rid]

    return {
        "region_start_registered": rid in area.region_start,
        "region_start_reachable_from_spawn": _reachable(tm, start_tile, region_tile),
        "region_has_enemies": len(region_enemies) > 0,
        "region_enemy_levels_match_spec":
            all(e.area_level == spec.area_level for e in region_enemies),
        "region_has_boss": len(region_bosses) == 1,
        "region_boss_alive": all(b.alive for b in region_bosses),
        "region_registered_its_own_gate": len(new_gates) == 1,
        "region_gate_closed": ng is not None and ng.state == "closed",
        "region_gate_tile_is_locked_door":
            ng is not None and tm.grid[ng.tile_row][ng.tile_col] == DOOR_CLOSED,
        "invariants_clean": _check_invariants(game) == [],
        "invariant_errors": _check_invariants(game),
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 10. Staging / reroll / push-back never touch the map
# ---------------------------------------------------------------------------

def test_staging_reroll_pushback_leave_map_untouched():
    game = _make_game(seed=5)
    _normalize_clock(game)
    tm = game.current_area.tilemap
    snap = copy.deepcopy(tm.grid)
    dims = (tm.cols, tm.rows)
    rects = len(game.current_area.region_rects)
    enemies = len(game.current_area.enemies)

    game._generate_next_extension(mode=config.EXTENSION_MODE_MANUAL)
    for _ in range(config.REROLL_CAP):
        game._reroll_pending_extension()
    staged_unchanged = (tm.grid == snap and (tm.cols, tm.rows) == dims)

    game._discard_pending_extension()
    pushed_unchanged = (tm.grid == snap and (tm.cols, tm.rows) == dims)

    return {
        "stage_and_rerolls_leave_grid_identical": staged_unchanged,
        "pushback_leaves_grid_identical": pushed_unchanged,
        "no_region_added": len(game.current_area.region_rects) == rects,
        "no_enemies_added": len(game.current_area.enemies) == enemies,
        "door_still_locked":
            all(tm.grid[g.tile_row][g.tile_col] == DOOR_CLOSED
                for g in game.world.closed_gates()),
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 11. Accept with no sealed door left
# ---------------------------------------------------------------------------

def test_accept_without_gate_is_safe_noop():
    game = _make_game(seed=6)
    _normalize_clock(game)
    ext_before = len(game.world.extensions)
    dims = (game.current_area.tilemap.cols, game.current_area.tilemap.rows)

    game._generate_next_extension(mode=config.EXTENSION_MODE_MANUAL)
    game._pending_gate = None            # simulate: every gate already open

    try:
        game._accept_pending_extension()
        crashed = False
    except Exception as e:
        crashed = f"{type(e).__name__}: {e}"

    return {
        "no_crash": crashed is False,
        "no_extension_added": len(game.world.extensions) == ext_before,
        "map_unchanged":
            (game.current_area.tilemap.cols, game.current_area.tilemap.rows) == dims,
        "pending_cleared": game._pending_extension_spec is None,
        "banner_dismissed": not game.evolution_banner.visible,
        "errors": [] if crashed is False else [str(crashed)],
    }


# ---------------------------------------------------------------------------
# 12. Chained expansions
# ---------------------------------------------------------------------------

def test_chained_expansions_keep_invariants():
    game = _make_game(seed=7)
    results = []
    for _ in range(3):
        before = len(game.world.extensions)
        _stage_and_accept(game)
        results.append(len(game.world.extensions) == before + 1)

    open_n = len(game.world.open_gates())
    closed_n = len(game.world.closed_gates())

    return {
        "all_three_accepted": all(results),
        "three_regions_registered": len(game.current_area.region_rects) == 3,
        "open_gates_equal_extensions": open_n == 3,
        "one_closed_gate_per_region_plus_leftovers": closed_n >= 1,
        "starting_gate_opened_first":
            game.world.gates_in_area("starting_area")[0].is_open(),
        "invariants_clean": _check_invariants(game) == [],
        "invariant_errors": _check_invariants(game),
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 13. Multi-seed stress
# ---------------------------------------------------------------------------

def test_multi_seed_stress():
    problems = []
    completed = 0
    for seed in range(6):
        game = _make_game(seed=100 + seed)
        for step in range(3):
            before = len(game.world.extensions)
            _stage_and_accept(game)
            if len(game.world.extensions) != before + 1:
                problems.append(f"seed {100 + seed} step {step}: expansion refused "
                                f"(no closed gate / site?)")
                break
            completed += 1
            for e in _check_invariants(game):
                problems.append(f"seed {100 + seed} step {step}: {e}")
    return {
        "expansions_completed": completed,
        "expected": 18,
        "problems": problems[:10],
        "errors": problems[:10],
    }


# ---------------------------------------------------------------------------
# 14. Determinism
# ---------------------------------------------------------------------------

def test_expansion_is_deterministic():
    a = _make_game(seed=2024)
    _stage_and_accept(a)
    grid_a = copy.deepcopy(a.current_area.tilemap.grid)
    rects_a = [(rid, tuple(r)) for rid, r in a.current_area.region_rects]
    gates_a = [g.to_dict() for g in a.world.gates]

    b = _make_game(seed=2024)
    _stage_and_accept(b)
    grid_b = b.current_area.tilemap.grid
    rects_b = [(rid, tuple(r)) for rid, r in b.current_area.region_rects]
    _ignore = ("opened_at", "gate_id")
    gates_b = [{k: v for k, v in g.to_dict().items() if k not in _ignore}
               for g in b.world.gates]
    gates_a_cmp = [{k: v for k, v in g.items() if k not in _ignore}
                   for g in gates_a]

    return {
        "grids_identical": grid_a == grid_b,
        "region_rects_identical": rects_a == rects_b,
        "gates_identical_ignoring_timestamps": gates_a_cmp == gates_b,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 15. Region lookup + _update_current_region
# ---------------------------------------------------------------------------

def test_region_lookup_and_area_entered_events():
    game = _make_game(seed=8)
    spec, gate = _stage_and_accept(game)
    rid = spec.extension_id
    area = game.current_area
    ts = config.TILE_SIZE

    start_tile = _tile(*area.start_pos)
    region_tile = _tile(*area.region_start[rid])
    corridor_tiles = [t for t, r in area.tile_region.items() if r == rid]

    log = game.event_log
    base = len(log.events_of_type("area_entered"))

    game.player.x, game.player.y = area.region_start[rid]
    game._update_current_region()
    after_enter = len(log.events_of_type("area_entered"))
    game._update_current_region()          # no change -> no new event
    after_repeat = len(log.events_of_type("area_entered"))

    return {
        "start_tile_is_starting_area":
            area.region_at_tile(*start_tile) == "starting_area",
        "region_start_tile_maps_to_region":
            area.region_at_tile(*region_tile) == rid,
        "pixel_lookup_matches_tile_lookup":
            area.region_at_pixel(region_tile[0] * ts + 1, region_tile[1] * ts + 1) == rid,
        "corridor_tiles_recorded": len(corridor_tiles) > 0,
        "corridor_tile_maps_to_region":
            all(area.region_at_tile(*t) == rid for t in corridor_tiles[:20]),
        "current_region_switched": game.current_area_id == rid,
        "current_extension_id_switched": game.current_extension_id == rid,
        "area_entered_emitted_once": after_enter == base + 1,
        "no_duplicate_event_when_unchanged": after_repeat == after_enter,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 16. Fast travel
# ---------------------------------------------------------------------------

def test_fast_travel_and_gateway_menu():
    game = _make_game(seed=9)

    # nothing to travel to yet -> menu must NOT open
    game._open_gateway_menu()
    menu_closed_when_empty = not game.gateway_menu.visible

    spec, _ = _stage_and_accept(game)
    rid = spec.extension_id

    game._open_gateway_menu()
    labels = [e["label"] for e in game.gateway_menu.entries]
    targets = [e["target_area_id"] for e in game.gateway_menu.entries]
    menu_open = game.gateway_menu.visible
    game.gateway_menu.hide()

    game._teleport_to_region(rid)
    at_region = (game.current_area_id == rid
                 and (game.player.x, game.player.y) == game.current_area.region_start[rid])

    # inside the region the menu offers a way back
    game._open_gateway_menu()
    back_offered = "starting_area" in [e["target_area_id"]
                                       for e in game.gateway_menu.entries]
    # selecting it goes through the real handler
    game._handle_gateway_menu_select("starting_area")
    back_home = (game.current_area_id == "starting_area"
                 and game.current_extension_id is None
                 and (game.player.x, game.player.y) == game.current_area.start_pos)

    # unknown target is ignored, not a crash
    before = (game.player.x, game.player.y)
    try:
        game._teleport_to_region("nope")
        bad_ok = (game.player.x, game.player.y) == before
    except Exception:
        bad_ok = False

    return {
        "menu_stays_closed_when_nothing_to_visit": menu_closed_when_empty,
        "menu_opens_after_expansion": menu_open,
        "menu_lists_new_region": rid in targets,
        "menu_label_mentions_name": any(spec.extension_name in l for l in labels),
        "teleport_lands_on_region_start": at_region,
        "menu_offers_return_inside_region": back_offered,
        "select_returns_to_start": back_home,
        "teleport_unknown_region_is_noop": bad_ok,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 17. Camera follows map growth
# ---------------------------------------------------------------------------

def test_camera_world_size_follows_growth():
    game = _make_game(seed=10)
    w0, h0 = game.camera.world_w, game.camera.world_h
    _stage_and_accept(game)
    tm = game.current_area.tilemap
    pw, ph = tm.pixel_size()

    game._teleport_to_region(list(game.current_area.region_start.keys())[-1])
    cam_in_bounds = (0 <= game.camera.x <= max(0, pw - config.SCREEN_WIDTH)
                     and 0 <= game.camera.y <= max(0, ph - config.SCREEN_HEIGHT))

    return {
        "camera_matches_new_pixel_size": (game.camera.world_w, game.camera.world_h) == (pw, ph),
        "camera_actually_grew": game.camera.world_w > w0 or game.camera.world_h > h0,
        "camera_clamped_inside_new_map": cam_in_bounds,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 18. Exploration + profile
# ---------------------------------------------------------------------------

def test_exploration_and_profile_after_expansion():
    game = _make_game(seed=11)
    spec, _ = _stage_and_accept(game)
    rid = spec.extension_id

    total = game.exploration.area_totals.get(rid)
    counted = game.current_area.region_walkable_count(rid)

    game._teleport_to_region(rid)
    game.exploration._timer = 999
    game.exploration.update(0.0, rid, game.player.x, game.player.y)
    frac = game.exploration.fraction_for(rid)

    profile = game._current_profile()

    return {
        "region_registered_in_exploration": total is not None and total >= 1,
        "total_matches_rect_floor_count": total == max(1, counted),
        "fraction_in_range": 0.0 < frac <= 1.0,
        "profile_builds": profile is not None,
        "profile_counts_extension": profile.extensions_generated == 1,
        "profile_average_in_range": 0.0 <= profile.average_exploration <= 1.0,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 19. Kill pipeline -- starting area (regression: XP + loot + removal)
# ---------------------------------------------------------------------------

def test_kill_grants_xp_loot_and_removes_enemy():
    game = _make_game(seed=12)
    rules = game._active_world_rules()
    enemy = _enemy_in_region(game, "starting_area")
    enemy.hp = 1
    enemy.is_elite = False

    # force the loot roll to succeed
    game.world.rng_service.get_stream("loot").random = lambda: 0.0

    lvl0, xp0 = game.player.progression.level, game.player.progression.xp
    drops0 = len(game.current_area.loot_drops)
    kills0 = len(game.event_log.events_of_type("enemy_killed"))

    game._apply_player_hit(enemy, rules)

    gained = (game.player.progression.level > lvl0
              or game.player.progression.xp > xp0)

    return {
        "enemy_is_dead": enemy.alive is False,
        "enemy_removed_from_area": enemy not in game.current_area.enemies,
        "xp_granted": gained,
        "loot_dropped": len(game.current_area.loot_drops) == drops0 + 1,
        "enemy_killed_event_logged":
            len(game.event_log.events_of_type("enemy_killed")) == kills0 + 1,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 20. Kill pipeline -- inside an expansion region
# ---------------------------------------------------------------------------

def test_region_kill_tags_loot_with_region_id():
    game = _make_game(seed=13)
    spec, _ = _stage_and_accept(game)
    rid = spec.extension_id
    rules = game._active_world_rules()

    enemy = _enemy_in_region(game, rid)
    if enemy is None:
        return {"errors": ["no enemy found in the new region"]}
    enemy.hp = 1
    game.world.rng_service.get_stream("loot").random = lambda: 0.0

    drops0 = len(game.current_area.loot_drops)
    game._apply_player_hit(enemy, rules)

    drop = game.current_area.loot_drops[-1] if game.current_area.loot_drops else None
    ctx = drop["item"].creation_context if drop else {}

    return {
        "enemy_removed": enemy not in game.current_area.enemies,
        "loot_dropped": len(game.current_area.loot_drops) == drops0 + 1,
        "loot_tagged_with_region_id": ctx.get("extension_id") == rid,
        "loot_area_level_matches_spec": ctx.get("area_level") == enemy.area_level,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 21. Bosses list
# ---------------------------------------------------------------------------

def test_bosses_list_targets_hud_kill_and_projectile():
    from combat.attack_patterns import PlayerProjectile

    game = _make_game(seed=14)
    area = game.current_area
    rules = game._active_world_rules()
    boss = area.bosses[0]

    far_none = game._nearest_boss() is None
    in_targets = boss in game._live_attack_targets()

    game.player.x, game.player.y = boss.x - 60, boss.y
    near_found = game._nearest_boss() is boss

    # projectile reaches the boss through the bosses loop
    hp0 = boss.hp
    proj = PlayerProjectile(boss.x, boss.y, 0, 0, "physical", 1.0, rules,
                            piercing=False, max_range=100)
    game.player_projectiles.append(proj)
    game._update_player_projectiles(0.0)
    proj_hit = boss.hp < hp0

    # kill through the shared hit path
    boss.hp = 1
    kills0 = len(game.event_log.events_of_type("boss_killed"))
    game._apply_player_hit(boss, rules)

    return {
        "no_boss_shown_when_far": far_none,
        "alive_boss_is_attackable": in_targets,
        "nearest_boss_found_when_close": near_found,
        "projectile_damages_boss": proj_hit,
        "boss_dead": boss.alive is False,
        "boss_killed_event_logged":
            len(game.event_log.events_of_type("boss_killed")) == kills0 + 1,
        "dead_boss_not_a_target": boss not in game._live_attack_targets(),
        "dead_boss_not_nearest": game._nearest_boss() is None,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 22. Update culling
# ---------------------------------------------------------------------------

def test_update_skips_far_entities_and_ticks_near_ones():
    game = _make_game(seed=15)
    _normalize_clock(game)
    area = game.current_area
    px, py = game.player.x, game.player.y

    far = next((e for e in area.enemies
                if abs(e.x - px) > 1200 or abs(e.y - py) > 1200), None)
    if far is None:
        return {"errors": ["no far enemy available to test culling"]}
    far._attack_timer = 5.0

    near = area.enemies[0] if area.enemies[0] is not far else area.enemies[1]
    near.x, near.y = px + 150, py          # inside aggro range, outside contact
    near._attack_timer = 5.0

    boss = area.bosses[0]
    boss._cooldown_timer = 5.0             # far from spawn -> must not tick

    game._update(0.1)

    return {
        "far_enemy_not_updated": far._attack_timer == 5.0,
        "near_enemy_updated": near._attack_timer < 5.0,
        "far_boss_not_updated": boss._cooldown_timer == 5.0,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 23. Boss melee: one take_damage call per hit
# ---------------------------------------------------------------------------

def test_boss_melee_hits_player_once():
    """
    Fails until the duplicated `player.take_damage(...)` lines after each
    `_hit_player(...)` in enemies/boss.py (projectile / melee / dash) are
    removed.
    """
    game = _make_game(seed=16)
    boss = game.current_area.bosses[0]
    player = game.player
    player.x, player.y = boss.x, boss.y
    player._invuln_timer = 0.0

    calls = []
    orig = player.take_damage

    def counting(amount, cause="physical"):
        calls.append(amount)
        return orig(amount, cause)

    player.take_damage = counting
    try:
        boss._pending_action = "melee"
        boss._execute_attack(player, game.event_bus, boss.current_phase,
                             game._active_world_rules())
    finally:
        player.take_damage = orig

    return {
        "take_damage_called_once": len(calls) == 1,
        "calls": len(calls),
        "errors": [] if len(calls) == 1 else
                  [f"take_damage called {len(calls)}x per boss melee hit"],
    }


# ---------------------------------------------------------------------------
# 24. Respawn
# ---------------------------------------------------------------------------

def test_respawn_uses_current_region_start():
    game = _make_game(seed=17)
    spec, _ = _stage_and_accept(game)
    rid = spec.extension_id

    game._teleport_to_region(rid)
    game.player.alive = False
    game.player.x, game.player.y = 0, 0
    game._respawn_player()
    in_region = (game.player.x, game.player.y) == game.current_area.region_start[rid]

    game._teleport_to_region("starting_area")
    game.player.alive = False
    game._respawn_player()
    at_home = (game.player.x, game.player.y) == game.current_area.start_pos

    return {
        "respawn_in_region_start": in_region,
        "respawn_home_in_starting_area": at_home,
        "player_alive_again": game.player.alive is True,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 25. Save / load replay
# ---------------------------------------------------------------------------

def test_save_load_rebuilds_identical_map():
    game = _make_game(seed=18)
    tmp = tempfile.mkdtemp(prefix="arpg_test_")
    try:
        game.save_manager.path = os.path.join(tmp, "save.json")
        game.save_manager.stash_path = os.path.join(tmp, "stash.json")

        _stage_and_accept(game)
        spec2, _ = _stage_and_accept(game)
        rid1 = game.world.extensions[0].extension_id
        game._teleport_to_region(rid1)

        tm = game.current_area.tilemap
        snap_grid = copy.deepcopy(tm.grid)
        snap_dims = (tm.cols, tm.rows)
        snap_rects = [(r, tuple(rc)) for r, rc in game.current_area.region_rects]
        snap_gates = [g.to_dict() for g in game.world.gates]
        snap_ext_ids = [e.extension_id for e in game.world.extensions]
        snap_enemy_counts = {}
        for e in game.current_area.enemies:
            k = getattr(e, "region_id", "starting_area")
            snap_enemy_counts[k] = snap_enemy_counts.get(k, 0) + 1
        snap_pos = (game.player.x, game.player.y)

        game._save_game()
        # wreck in-memory state, then restore from disk
        game.player.x, game.player.y = 0, 0
        game._load_game()

        area = game.current_area
        tm2 = area.tilemap
        new_counts = {}
        for e in area.enemies:
            k = getattr(e, "region_id", "starting_area")
            new_counts[k] = new_counts.get(k, 0) + 1

        return {
            "grid_identical_after_load": tm2.grid == snap_grid,
            "dims_identical": (tm2.cols, tm2.rows) == snap_dims,
            "region_rects_identical":
                [(r, tuple(rc)) for r, rc in area.region_rects] == snap_rects,
            "gates_identical": [g.to_dict() for g in game.world.gates] == snap_gates,
            "extensions_identical":
                [e.extension_id for e in game.world.extensions] == snap_ext_ids,
            "enemy_counts_per_region_match": new_counts == snap_enemy_counts,
            "player_position_restored": (game.player.x, game.player.y) == snap_pos,
            "current_region_restored": game.current_area_id == rid1,
            "camera_matches_loaded_map":
                (game.camera.world_w, game.camera.world_h) == tm2.pixel_size(),
            "exploration_has_all_regions":
                all(e in game.exploration.area_totals for e in snap_ext_ids),
            "invariants_clean": _check_invariants(game) == [],
            "invariant_errors": _check_invariants(game),
            "errors": [],
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_load_then_expand_again_still_works():
    game = _make_game(seed=19)
    tmp = tempfile.mkdtemp(prefix="arpg_test_")
    try:
        game.save_manager.path = os.path.join(tmp, "save.json")
        game.save_manager.stash_path = os.path.join(tmp, "stash.json")
        _stage_and_accept(game)
        game._save_game()
        game._load_game()

        before = len(game.world.extensions)
        _stage_and_accept(game)

        return {
            "second_expansion_after_load_accepted":
                len(game.world.extensions) == before + 1,
            "invariants_clean": _check_invariants(game) == [],
            "invariant_errors": _check_invariants(game),
            "errors": [],
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 26. F4 toggle + draw smoke tests
# ---------------------------------------------------------------------------

def test_f4_toggle_and_draw_smoke():
    game = _make_game(seed=20)
    errs = []

    def attempt(label, fn):
        try:
            fn()
            return True
        except Exception as e:
            errs.append(f"{label}: {type(e).__name__}: {e}")
            return False

    start_off = game.show_region_details is False
    game._handle_keydown(pygame.K_F4)
    on = game.show_region_details is True
    game._handle_keydown(pygame.K_F4)
    off_again = game.show_region_details is False

    ok = {}
    ok["start_plain"] = attempt("start plain", game._draw)
    game.show_region_details = True
    ok["start_details"] = attempt("start details", game._draw)

    spec, _ = _stage_and_accept(game)
    game._teleport_to_region(spec.extension_id)
    game.show_region_details = False
    ok["region_plain"] = attempt("region plain", game._draw)
    game.show_region_details = True
    ok["region_details"] = attempt("region details", game._draw)

    game._open_gateway_menu()
    ok["gateway_menu"] = attempt("gateway menu", game._draw)
    game.gateway_menu.hide()

    ok["update_in_region"] = attempt("update", lambda: game._update(0.016))
    ok["debug_world_info"] = attempt("F7 info", game._debug_print_world_info)

    return {
        "f4_starts_off": start_off,
        "f4_turns_on": on,
        "f4_turns_off": off_again,
        **{f"draw_ok_{k}": v for k, v in ok.items()},
        "errors": errs,
    }


# ---------------------------------------------------------------------------
# 27. Backward-compat: Extension.ensure_built
# ---------------------------------------------------------------------------

def test_extension_ensure_built_still_works():
    from world.world import World
    from world.extension import ExtensionSpec

    world = World(77)
    spec = ExtensionSpec(extension_id="legacy_1", extension_name="Legacy",
                         area_level=3, biome="ash_canyon", room_count=5)
    ext = world.add_extension(spec)
    area = ext.ensure_built(world.rng_service, world=world)
    again = ext.ensure_built(world.rng_service, world=world)

    gates = world.gates_in_area("legacy_1")
    return {
        "returns_area_dict": isinstance(area, dict) and "tilemap" in area,
        "build_is_cached": again is area,
        "registers_exactly_one_gate": len(gates) == 1,
        "gate_defaults_to_east_side": gates[0].side == "e" if gates else False,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    tests = [
        ("test_tilemap_tile_semantics", test_tilemap_tile_semantics),
        ("test_gate_side_roundtrip_and_defaults", test_gate_side_roundtrip_and_defaults),
        ("test_exploration_register_region", test_exploration_register_region),
        ("test_find_door_site", test_find_door_site),
        ("test_stamp_door_only_for_closed_gates", test_stamp_door_only_for_closed_gates),
        ("test_starting_area_has_locked_door", test_starting_area_has_locked_door),
        ("test_locked_door_blocks_then_opens", test_locked_door_blocks_then_opens),
        ("test_expansion_grows_map_beyond_old_edge", test_expansion_grows_map_beyond_old_edge),
        ("test_new_region_connected_populated_and_has_next_door",
         test_new_region_connected_populated_and_has_next_door),
        ("test_staging_reroll_pushback_leave_map_untouched",
         test_staging_reroll_pushback_leave_map_untouched),
        ("test_accept_without_gate_is_safe_noop", test_accept_without_gate_is_safe_noop),
        ("test_chained_expansions_keep_invariants", test_chained_expansions_keep_invariants),
        ("test_multi_seed_stress", test_multi_seed_stress),
        ("test_expansion_is_deterministic", test_expansion_is_deterministic),
        ("test_region_lookup_and_area_entered_events",
         test_region_lookup_and_area_entered_events),
        ("test_fast_travel_and_gateway_menu", test_fast_travel_and_gateway_menu),
        ("test_camera_world_size_follows_growth", test_camera_world_size_follows_growth),
        ("test_exploration_and_profile_after_expansion",
         test_exploration_and_profile_after_expansion),
        ("test_kill_grants_xp_loot_and_removes_enemy",
         test_kill_grants_xp_loot_and_removes_enemy),
        ("test_region_kill_tags_loot_with_region_id",
         test_region_kill_tags_loot_with_region_id),
        ("test_bosses_list_targets_hud_kill_and_projectile",
         test_bosses_list_targets_hud_kill_and_projectile),
        ("test_update_skips_far_entities_and_ticks_near_ones",
         test_update_skips_far_entities_and_ticks_near_ones),
        ("test_boss_melee_hits_player_once", test_boss_melee_hits_player_once),
        ("test_respawn_uses_current_region_start", test_respawn_uses_current_region_start),
        ("test_save_load_rebuilds_identical_map", test_save_load_rebuilds_identical_map),
        ("test_load_then_expand_again_still_works",
         test_load_then_expand_again_still_works),
        ("test_f4_toggle_and_draw_smoke", test_f4_toggle_and_draw_smoke),
        ("test_extension_ensure_built_still_works",
         test_extension_ensure_built_still_works),
    ]
    failures = 0
    for name, fn in tests:
        r = _run(name, fn)
        bad_flags = [k for k, v in r.items() if v is False]
        if r.get("errors") or bad_flags:
            failures += 1
            if bad_flags:
                print(f"   !! failing checks in {name}: {bad_flags}\n")
    print(f"=== SUMMARY: {len(tests) - failures}/{len(tests)} tests clean ===")


if __name__ == "__main__":
    main()
