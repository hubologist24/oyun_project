import heapq
import math
import random

from world.tilemap import TileMap
from world.room_graph import RoomGraph, RoomType, build_room_graph, bfs_order


ROOM_BRACKET_SIZE          = 10
ROOM_MAX_EXTRA_CAP         = 2
ROOM_EXTRA_WEIGHTS         = (4, 3, 2)
DENSITY_PER_BRACKET        = 0.15
DENSITY_CAP                = 0.60
MAP_GROWTH_PER_EXTRA_ROOM  = 0.25
ROOM_SPACING = 3

# --- fit / routing tuning -------------------------------------------------
TILES_PER_GRAPH_NODE = 260      # map area reserved per room-graph node
EXTRA_RECT_REQUEST   = 4        # ask for a few spare rects, re-wall the unused ones
CORRIDOR_FLOOR_COST  = 60       # cutting through someone else's floor (last resort)
CORRIDOR_HUG_COST    = 20       # running alongside foreign floor (would merge with it)
CORRIDOR_TURN_COST   = 2        # keeps corridors straight instead of staircases
GATE_MIN_SPACING     = 3        # min Chebyshev distance between two doors

PROTECTED_ROOM_TYPES = {RoomType.ENTRANCE, RoomType.GUARDIAN,
                        RoomType.REWARD, RoomType.GATE}

_N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
INWARD = {"e": (-1, 0), "w": (1, 0), "s": (0, -1), "n": (0, 1)}
OUTWARD = {k: (-dx, -dy) for k, (dx, dy) in INWARD.items()}
_SIDE_FROM_INWARD = {v: k for k, v in INWARD.items()}


def _carve_h(grid, x0, x1, y):
    rows, cols = len(grid), len(grid[0]) if grid else 0
    for col in range(min(x0, x1), max(x0, x1) + 1):
        if 0 <= y < rows and 0 <= col < cols:
            grid[y][col] = 0
        if 0 <= y + 1 < rows and 0 <= col < cols:
            grid[y + 1][col] = 0


def _carve_v(grid, y0, y1, x):
    rows, cols = len(grid), len(grid[0]) if grid else 0
    for row in range(min(y0, y1), max(y0, y1) + 1):
        if 0 <= row < rows and 0 <= x < cols:
            grid[row][x] = 0
        if 0 <= row < rows and 0 <= x + 1 < cols:
            grid[row][x + 1] = 0


def extra_rooms_for_level(rng, area_level):
    rolls = list(range(ROOM_MAX_EXTRA_CAP + 1))
    return rng.choices(rolls, weights=list(ROOM_EXTRA_WEIGHTS), k=1)[0]


def _pick_gate_tile(grid, room):
    rows, cols = len(grid), len(grid[0]) if grid else 0
    x0, y0, x1, y1 = room
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2

    def open3(c, r):
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc = r + dr, c + dc
                if not (0 <= rr < rows and 0 <= cc < cols) or grid[rr][cc] != 0:
                    return False
        return True

    cands = [(c, r) for r in range(y0 + 1, y1 - 1)
             for c in range(x0 + 1, x1 - 1) if open3(c, r)]
    if not cands:
        return cx, cy
    cands.sort(key=lambda p: (-(abs(p[0] - cx) + abs(p[1] - cy)), p[1], p[0]))
    return cands[0]


def enemy_density_mult(area_level):
    bracket = area_level // ROOM_BRACKET_SIZE
    return 1.0 + min(DENSITY_CAP, max(0, bracket) * DENSITY_PER_BRACKET)


def _scale_map_for_rooms(cols, rows, extra_rooms):
    if extra_rooms <= 0:
        return cols, rows
    tiles_factor = 1.0 + MAP_GROWTH_PER_EXTRA_ROOM * extra_rooms
    dim_scale = math.sqrt(tiles_factor)
    return int(cols * dim_scale) + 2, int(rows * dim_scale) + 2


def _fit_map_to_nodes(cols, rows, n_nodes):
    """Grow the map so the room graph physically fits (labyrinths reach ~20 nodes)."""
    need = n_nodes * TILES_PER_GRAPH_NODE
    have = max(1, cols * rows)
    if have >= need:
        return cols, rows
    s = math.sqrt(need / have)
    return int(math.ceil(cols * s)), int(math.ceil(rows * s))


def generate_dungeon_grid(rng: random.Random, cols=50, rows=38, room_count=6,
                          min_room=6, max_room=12, min_rooms=3):
    grid = [[1 for _ in range(cols)] for _ in range(rows)]
    rooms = []
    target = max(int(room_count), int(min_rooms))

    for attempt_min in (min_room, 4, 3):
        if len(rooms) >= target:
            break
        attempts, max_attempts = 0, target * 60
        while len(rooms) < target and attempts < max_attempts:
            attempts += 1
            w_max = min(max_room, cols - 4)
            h_max = min(max_room, rows - 4)
            if w_max < attempt_min or h_max < attempt_min:
                break
            w = rng.randint(attempt_min, w_max)
            h = rng.randint(attempt_min, h_max)
            if cols - w - 2 < 1 or rows - h - 2 < 1:
                continue
            x0 = rng.randint(1, cols - w - 2)
            y0 = rng.randint(1, rows - h - 2)
            x1, y1 = x0 + w, y0 + h
            candidate = (x0, y0, x1, y1)
            overlaps = any(
                not (x1 < rx0 - ROOM_SPACING or x0 > rx1 + ROOM_SPACING
                     or y1 < ry0 - ROOM_SPACING or y0 > ry1 + ROOM_SPACING)
                for (rx0, ry0, rx1, ry1) in rooms
            )
            if overlaps:
                continue
            rooms.append(candidate)
            for row in range(y0, y1):
                for col in range(x0, x1):
                    grid[row][col] = 0

    rooms.sort(key=lambda r: (r[0], r[1]))
    return grid, rooms


def _room_center(rect):
    x0, y0, x1, y1 = rect
    return ((x0 + x1) // 2, (y0 + y1) // 2)


def _rect_tiles(rect, rows, cols):
    x0, y0, x1, y1 = rect
    for r in range(max(0, y0), min(rows, y1)):
        for c in range(max(0, x0), min(cols, x1)):
            yield (c, r)


def _in_rect(t, rect):
    return rect[0] <= t[0] < rect[2] and rect[1] <= t[1] < rect[3]


def _connect_rooms(grid, a, b, rng):
    """Legacy L-corridor (kept for reference / fallback). Cuts through anything."""
    cx0, cy0 = _room_center(a)
    cx1, cy1 = _room_center(b)
    if rng.random() < 0.5:
        _carve_h(grid, cx0, cx1, cy0)
        _carve_v(grid, cy0, cy1, cx1)
    else:
        _carve_v(grid, cy0, cy1, cx0)
        _carve_h(grid, cx0, cx1, cy1)


# ---------------------------------------------------------------------------
# corridor routing
# ---------------------------------------------------------------------------
def route_corridor(grid, sources, goal_rect, friendly_rects=(), blocked=()):
    """
    A* from any source tile to any tile inside goal_rect, walking through walls.
    Floor that is not in goal_rect / friendly_rects is 'foreign': crossing it or
    running next to it is expensive, so corridors never create shortcuts unless
    there is literally no other way. Door tiles (values 2/3) are impassable.
    Returns the tile path (source ... goal) or None.
    """
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    gx0, gy0, gx1, gy1 = goal_rect
    blocked = set(blocked)
    friendly = tuple(friendly_rects)

    def in_goal(c, r):
        return gx0 <= c < gx1 and gy0 <= r < gy1

    def foreign(c, r):
        if grid[r][c] != 0 or in_goal(c, r):
            return False
        for fx0, fy0, fx1, fy1 in friendly:
            if fx0 <= c < fx1 and fy0 <= r < fy1:
                return False
        return True

    memo = {}

    def step_cost(c, r):
        v = memo.get((c, r))
        if v is None:
            if in_goal(c, r):
                v = 1
            elif foreign(c, r):
                v = CORRIDOR_FLOOR_COST
            else:
                v = 1
                for dc, dr in _N4:
                    nc, nr = c + dc, r + dr
                    if 0 <= nr < rows and 0 <= nc < cols and foreign(nc, nr):
                        v = CORRIDOR_HUG_COST
                        break
            memo[(c, r)] = v
        return v

    def h(c, r):
        dx = gx0 - c if c < gx0 else (c - gx1 + 1 if c >= gx1 else 0)
        dy = gy0 - r if r < gy0 else (r - gy1 + 1 if r >= gy1 else 0)
        return dx + dy

    heap, best, prev = [], {}, {}
    tick = 0
    for s in sources:
        key = (s, None)
        if key in best:
            continue
        best[key] = 0
        heap.append((h(*s), tick, 0, s, None))
        tick += 1
    heapq.heapify(heap)

    while heap:
        _, _, d, t, dv = heapq.heappop(heap)
        if d != best.get((t, dv)):
            continue
        if in_goal(*t):
            path, key = [t], (t, dv)
            while key in prev:
                key = prev[key]
                path.append(key[0])
            path.reverse()
            return path
        c, r = t
        for dvec in _N4:
            nc, nr = c + dvec[0], r + dvec[1]
            if not (0 <= nr < rows and 0 <= nc < cols):
                continue
            n = (nc, nr)
            if n in blocked or grid[nr][nc] not in (0, 1):
                continue
            nd = d + step_cost(nc, nr)
            if dv is not None and dvec != dv:
                nd += CORRIDOR_TURN_COST
            nk = (n, dvec)
            if nd < best.get(nk, 1 << 30):
                best[nk] = nd
                prev[nk] = (t, dv)
                tick += 1
                heapq.heappush(heap, (nd + h(nc, nr), tick, nd, n, dvec))
    return None


def carve_corridor(grid, path, friendly_rects=(), goal_rect=None, blocked=(), widen=True):
    """Carve a routed path; optionally widen to 2 tiles where that can't merge with foreign floor."""
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    pset = set(path)
    blocked = set(blocked)
    rects = tuple(friendly_rects) + ((goal_rect,) if goal_rect else ())

    def allowed_floor(c, r):
        if (c, r) in pset:
            return True
        return any(x0 <= c < x1 and y0 <= r < y1 for x0, y0, x1, y1 in rects)

    extra, taken = [], set()
    if widen:
        for (c0, r0), (c1, r1) in zip(path, path[1:]):
            wc, wr = (c1, r1 + 1) if r0 == r1 else (c1 + 1, r1)
            w = (wc, wr)
            if w in pset or w in taken or w in blocked:
                continue
            if not (0 <= wr < rows and 0 <= wc < cols) or grid[wr][wc] != 1:
                continue
            ok = True
            for dc, dr in _N4:
                nc, nr = wc + dc, wr + dr
                if (0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == 0
                        and not allowed_floor(nc, nr) and (nc, nr) not in taken):
                    ok = False
                    break
            if ok:
                taken.add(w)
                extra.append(w)

    for c, r in list(path) + extra:
        if grid[r][c] == 1:
            grid[r][c] = 0
    return len(path) + len(extra)


def _connect_rooms_routed(grid, a, b):
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    sources = [t for t in _rect_tiles(a, rows, cols) if grid[t[1]][t[0]] == 0]
    if not sources:
        sources = [_room_center(a)]
    path = route_corridor(grid, sources, b, friendly_rects=(a,))
    if not path:
        cx0, cy0 = _room_center(a)
        cx1, cy1 = _room_center(b)
        _carve_h(grid, cx0, cx1, cy0)
        _carve_v(grid, cy0, cy1, cx1)
        return
    carve_corridor(grid, path, friendly_rects=(a,), goal_rect=b)


# ---------------------------------------------------------------------------
# graph fitting
# ---------------------------------------------------------------------------
def fit_graph_to_capacity(graph, capacity):
    """
    Remove optional nodes until the graph fits `capacity` rects. Leaves go first,
    then non-hub nodes, deepest first. Neighbours of a removed node are chained
    together so connectivity (and the entrance->guardian->reward->gate spine)
    is preserved. Protected room types are never removed.
    Returns (new_graph, removed_count).
    """
    n = len(graph.nodes)
    if capacity >= n:
        return graph, 0
    adj = {i: set(graph.neighbors(i)) for i in range(n)}
    alive = set(range(n))
    while len(alive) > max(1, capacity):
        cands = [i for i in alive
                 if graph.nodes[i].room_type not in PROTECTED_ROOM_TYPES]
        if not cands:
            break
        victim = max(cands, key=lambda i: (
            len(adj[i]) <= 1,
            graph.nodes[i].tags.get("role") is None,
            graph.nodes[i].depth,
            i,
        ))
        nbs = sorted(adj.pop(victim))
        alive.discard(victim)
        for x in nbs:
            adj[x].discard(victim)
        for x, y in zip(nbs, nbs[1:]):
            adj[x].add(y)
            adj[y].add(x)

    keep = sorted(alive)
    remap = {old: new for new, old in enumerate(keep)}
    g2 = RoomGraph(graph.archetype)
    for old in keep:
        nd = graph.nodes[old]
        g2.add_node(nd.room_type, nd.depth, nd.branch, **dict(nd.tags))
    for x in keep:
        for y in adj[x]:
            if x < y:
                g2.connect(remap[x], remap[y])
    return g2, n - len(keep)


def _first_index(graph, rtype, default):
    return next((nd.index for nd in graph.nodes if nd.room_type == rtype), default)


# ---------------------------------------------------------------------------
# gate sites
# ---------------------------------------------------------------------------
def pick_wall_gate(grid, room, sides=("e", "s"), strict=False, exclude=(),
                   min_spacing=GATE_MIN_SPACING):
    """
    Pick a door tile in the wall ring of `room`. A good site has wall on both
    sides along the wall, floor inward, and solid wall outward (so the next
    region's corridor can start there). strict=True returns None instead of
    falling back. `exclude` keeps doors at least `min_spacing` apart.
    """
    rows, cols = len(grid), len(grid[0]) if grid else 0
    x0, y0, x1, y1 = room
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    exclude = list(exclude)

    def wall(c, r):
        return 0 <= r < rows and 0 <= c < cols and grid[r][c] == 1

    def floor(c, r):
        return 0 <= r < rows and 0 <= c < cols and grid[r][c] == 0

    def spaced(c, r):
        return all(max(abs(c - ec), abs(r - er)) >= min_spacing for ec, er in exclude)

    for side in sides:
        cands = []
        if side == "e":
            c = x1
            for r in range(y0 + 1, y1 - 1):
                if (wall(c, r) and wall(c, r - 1) and wall(c, r + 1)
                        and floor(c - 1, r) and wall(c + 1, r) and spaced(c, r)):
                    cands.append((abs(r - cy), r, c))
        elif side == "w":
            c = x0 - 1
            for r in range(y0 + 1, y1 - 1):
                if (wall(c, r) and wall(c, r - 1) and wall(c, r + 1)
                        and floor(c + 1, r) and wall(c - 1, r) and spaced(c, r)):
                    cands.append((abs(r - cy), r, c))
        elif side == "n":
            r = y0 - 1
            for c in range(x0 + 1, x1 - 1):
                if (wall(c, r) and wall(c - 1, r) and wall(c + 1, r)
                        and floor(c, r + 1) and wall(c, r - 1) and spaced(c, r)):
                    cands.append((abs(c - cx), r, c))
        else:
            r = y1
            for c in range(x0 + 1, x1 - 1):
                if (wall(c, r) and wall(c - 1, r) and wall(c + 1, r)
                        and floor(c, r - 1) and wall(c, r + 1) and spaced(c, r)):
                    cands.append((abs(c - cx), r, c))
        if cands:
            cands.sort()
            _, r, c = cands[0]
            return c, r, side

    if strict:
        return None

    # relaxed fallback: any wall tile touching this room's floor; side from geometry
    for r in range(max(0, y0 - 1), min(rows, y1 + 1)):
        for c in range(max(0, x0 - 1), min(cols, x1 + 1)):
            if grid[r][c] != 1 or not spaced(c, r):
                continue
            for dc, dr in _N4:
                if floor(c + dc, r + dr) and _in_rect((c + dc, r + dr), room):
                    return c, r, _SIDE_FROM_INWARD[(dc, dr)]

    c, r = _pick_gate_tile(grid, room)
    return c, r, "e"


def plan_exit_gate_sites(grid, room, count, sides=("e", "s", "w", "n")):
    """
    Up to `count` distinct, spaced door sites. First pass: one per side.
    Second pass: allow a second door on an already-used side. Never duplicates.
    Shared by World.spawn_exit_gates_for_extension and tools/map_bot.
    """
    sites = []
    for _pass in range(2):
        for s in sides:
            if len(sites) >= count:
                return sites
            if _pass == 0 and any(x[2] == s for x in sites):
                continue
            res = pick_wall_gate(grid, room, sides=(s,), strict=True,
                                 exclude=[(c, r) for c, r, _ in sites])
            if res:
                sites.append(res)
    if not sites:
        sites.append(pick_wall_gate(grid, room, sides=sides))
    return sites[:count]


# ---------------------------------------------------------------------------
# area builder
# ---------------------------------------------------------------------------
def build_procedural_area(rng, area_level, cols=50, rows=38,
                          room_count=6, boss_room=True, extra_rooms=None,
                          room_graph=None, biome=None, seal_type=None):
    if extra_rooms is None:
        extra_rooms = extra_rooms_for_level(rng, area_level)

    if room_graph is None:
        room_graph, _, _, _ = build_room_graph(
            rng, target_rooms=room_count + extra_rooms + 3,
            biome=biome, seal_type=seal_type, has_guardian=boss_room,
        )

    cols, rows = _scale_map_for_rooms(cols, rows, extra_rooms)
    cols, rows = _fit_map_to_nodes(cols, rows, len(room_graph.nodes))
    n_nodes = len(room_graph.nodes)

    best = None
    for _attempt in range(3):
        cand = generate_dungeon_grid(rng, cols=cols, rows=rows,
                                     room_count=n_nodes + EXTRA_RECT_REQUEST)
        if best is None or len(cand[1]) > len(best[1]):
            best = cand
        if len(cand[1]) >= n_nodes:
            break
    grid, room_rects = best

    if not room_rects:
        fallback = (1, 1, max(3, min(cols - 1, 5)), max(3, min(rows - 1, 5)))
        grid = [[1 for _ in range(cols)] for _ in range(rows)]
        for r in range(fallback[1], fallback[3]):
            for c in range(fallback[0], fallback[2]):
                grid[r][c] = 0
        room_rects = [fallback]

    pruned = 0
    if len(room_rects) < len(room_graph.nodes):
        room_graph, pruned = fit_graph_to_capacity(room_graph, len(room_rects))

    entrance_idx = _first_index(room_graph, RoomType.ENTRANCE, 0)
    guardian_idx = _first_index(room_graph, RoomType.GUARDIAN, None)
    gate_idx = _first_index(room_graph, RoomType.GATE, len(room_graph.nodes) - 1)

    order = bfs_order(room_graph, entrance_idx)
    seen = set(order)
    order += [nd.index for nd in room_graph.nodes if nd.index not in seen]
    if len(order) > len(room_rects):
        # only when even the protected spine doesn't fit (degenerate maps)
        must = [i for i in (entrance_idx, guardian_idx, gate_idx) if i is not None]
        rest = [i for i in order if i not in must]
        keep = set(must) | set(rest[:max(0, len(room_rects) - len(must))])
        order = [i for i in order if i in keep][:len(room_rects)]

    node_to_room, node_to_room_index = {}, {}
    rooms, room_types = [], []
    for i, node_idx in enumerate(order):
        rect = room_rects[i]
        node_to_room[node_idx] = rect
        node_to_room_index[node_idx] = len(rooms)
        rooms.append(rect)
        room_types.append(room_graph.nodes[node_idx].room_type)

    # re-wall carved rects that no graph node uses (no orphan islands / empty chambers)
    for rect in room_rects[len(order):]:
        x0, y0, x1, y1 = rect
        for r in range(y0, y1):
            for c in range(x0, x1):
                grid[r][c] = 1

    for a, b in room_graph.edges:
        if a in node_to_room and b in node_to_room:
            _connect_rooms_routed(grid, node_to_room[a], node_to_room[b])

    tilemap = TileMap(grid)

    entrance_rect = node_to_room.get(entrance_idx, rooms[0] if rooms else None)
    guardian_rect = (node_to_room.get(guardian_idx)
                     if boss_room and guardian_idx is not None else None)
    gate_rect = node_to_room.get(gate_idx, rooms[-1] if rooms else None)
    if gate_rect is None and rooms:
        gate_rect = rooms[-1]
    if entrance_rect is None and rooms:
        entrance_rect = rooms[0]

    gate_room_index = node_to_room_index.get(gate_idx, len(rooms) - 1)
    if gate_rect is None:
        gate_tile_col, gate_tile_row, gate_side = 1, 1, "e"
    else:
        gate_tile_col, gate_tile_row, gate_side = plan_exit_gate_sites(
            tilemap.grid, gate_rect, 1)[0]

    enemy_types = {RoomType.COMBAT, RoomType.ELITE,
                   RoomType.CHALLENGE, RoomType.HAZARD}
    enemy_rooms = [r for r, t in zip(rooms, room_types) if t in enemy_types]
    if not enemy_rooms:
        enemy_rooms = [r for r in rooms
                       if r is not entrance_rect and r is not gate_rect]

    arena_seals = {"survival", "trial", "boss"}
    gate_room_arena = seal_type in arena_seals

    return {
        "tilemap": tilemap,
        "rooms": rooms,
        "room_types": room_types,
        "room_graph": room_graph,
        "node_to_room": node_to_room,
        "start_room": entrance_rect,
        "boss_room": guardian_rect,
        "gate_room": gate_rect,
        "gate_room_index": gate_room_index,
        "gate_tile_col": gate_tile_col,
        "gate_tile_row": gate_tile_row,
        "gate_side": gate_side,
        "enemy_rooms": enemy_rooms,
        "area_level": area_level,
        "biome": biome,
        "archetype": str(room_graph.archetype),
        "gate_room_arena": gate_room_arena,
        "pruned_nodes": pruned,
        "unused_rects": len(room_rects) - len(rooms),
    }
