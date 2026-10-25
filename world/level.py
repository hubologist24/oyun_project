"""
Level (starting area, hand-authored, unchanged shape from Phase 1/2)
plus AreaInstance (Phase 3: procedurally generated extension areas).

Both expose the same interface (tilemap, enemies, boss, loot_drops,
draw()) so core/game.py can treat "current playable area" uniformly
regardless of whether it's the curated start or a generated extension.
"""
import random
import pygame
from world.tilemap import TileMap
from enemies.enemy import spawn_basic_enemy
from enemies.boss import Boss, BossTemplate
from items.item_generator import generate_random_item
import core.config as config

from dataclasses import asdict


def _make_grid(cols, rows):
    return [[1 for _ in range(cols)] for _ in range(rows)]


def _carve_room(grid, x0, y0, x1, y1):
    for row in range(y0, y1):
        for col in range(x0, x1):
            grid[row][col] = 0


def _carve_corridor_h(grid, x0, x1, y):
    for col in range(min(x0, x1), max(x0, x1) + 1):
        grid[y][col] = 0
        grid[y + 1][col] = 0


def _carve_corridor_v(grid, y0, y1, x):
    for row in range(min(y0, y1), max(y0, y1) + 1):
        grid[row][x] = 0
        grid[row][x + 1] = 0


LEVEL_SPEC = {
    "cols": 46,
    "rows": 34,
    "rooms": {
        "start": (2, 2, 12, 12),
        "arena_a": (16, 2, 28, 12),
        "arena_b": (32, 2, 44, 12),
        "arena_c": (2, 16, 14, 28),
        "boss_room": (30, 16, 44, 30),
        "gate_room": (16, 16, 26, 24),   # NEW: dedicated small room for the starting gate
    },
    "enemy_counts": {"arena_a": 8, "arena_b": 11, "arena_c": 10},
    "loot_spots": 6,
}

class Level:
    """The curated starting area (Extension 0)."""

    def __init__(self, spec, rng_stream):
        self.spec = spec
        self.rng = rng_stream
        self.area_level = 1
        cols, rows = spec["cols"], spec["rows"]
        grid = _make_grid(cols, rows)

        rooms = spec["rooms"]
        for (x0, y0, x1, y1) in rooms.values():
            _carve_room(grid, x0, y0, x1, y1)

        sx0, sy0, sx1, sy1 = rooms["start"]
        ax0, ay0, ax1, ay1 = rooms["arena_a"]
        bx0, by0, bx1, by1 = rooms["arena_b"]
        cx0, cy0, cx1, cy1 = rooms["arena_c"]
        bossx0, bossy0, bossx1, bossy1 = rooms["boss_room"]
        gx0, gy0, gx1, gy1 = rooms["gate_room"]

        mid_y = (sy0 + sy1) // 2
        _carve_corridor_h(grid, sx1 - 1, ax0 + 1, mid_y)
        _carve_corridor_h(grid, ax1 - 1, bx0 + 1, mid_y)

        mid_x = (sx0 + sx1) // 2
        _carve_corridor_v(grid, sy1 - 1, cy0 + 1, mid_x)

        bx_mid = (bx0 + bx1) // 2
        _carve_corridor_v(grid, by1 - 1, bossy0 + 1, bx_mid)

        # Connect gate_room off the corridor between arena_c and boss_room
        gy_mid = (gy0 + gy1) // 2
        _carve_corridor_h(grid, cx1 - 1, gx0 + 1, gy_mid)

        self.tilemap = TileMap(grid)
        self.enemies = []
        self.loot_drops = []

        self._spawn_enemies()
        boss = Boss(*self._room_center(rooms["boss_room"]), BossTemplate.hollow_warden())
        boss.region_id = "starting_area"
        self.bosses = [boss]
        self.start_pos = self._room_center(rooms["start"])
        self.gate_room = rooms["gate_room"]

        self.region_rects = []          # [(region_id, pygame.Rect in tiles)]
        self.tile_region = {}           # (col,row) -> region_id for carved corridors
        self.region_start = {"starting_area": self.start_pos}
        self.region_loot_rules = {}

        self.shrines = []
        self._compute_safe_room_rect()
        self._spawn_starting_shrines()

    def _room_center(self, room):
        x0, y0, x1, y1 = room
        ts = config.TILE_SIZE
        return ((x0 + x1) / 2 * ts, (y0 + y1) / 2 * ts)

    def _spawn_enemies(self):
        rooms = self.spec["rooms"]
        counts = self.spec["enemy_counts"]
        ts = config.TILE_SIZE
        for room_name, count in counts.items():
            x0, y0, x1, y1 = rooms[room_name]
            area_level = {"arena_a": 2, "arena_b": 4, "arena_c": 3}.get(room_name, 1)
            for _ in range(count):
                col = self.rng.randint(x0 + 1, x1 - 2)
                row = self.rng.randint(y0 + 1, y1 - 2)
                x, y = col * ts + ts / 2, row * ts + ts / 2
                self.enemies.append(spawn_basic_enemy(x, y, self.rng, area_level=area_level))

    def _compute_safe_room_rect(self):
        x0, y0, x1, y1 = self.spec["rooms"]["start"]
        ts = config.TILE_SIZE
        self.safe_room_rect = pygame.Rect(
            x0 * ts, y0 * ts, (x1 - x0) * ts, (y1 - y0) * ts)

    def _spawn_starting_shrines(self):
        from world.shrine import make_shrine
        ts = config.TILE_SIZE
        x0, y0, x1, y1 = self.spec["rooms"]["start"]
        cx = ((x0 + x1) / 2) * ts
        cy = ((y0 + y1) / 2) * ts
        self.shrines.append(make_shrine("stash_chest", cx - 90, cy + 40, "starting_area"))
        self.shrines.append(make_shrine("rest_shrine", cx + 90, cy + 40, "starting_area"))

        gx0, gy0, gx1, gy1 = self.spec["rooms"]["gate_room"]
        gx = ((gx0 + gx1) / 2) * ts
        gy = ((gy0 + gy1) / 2) * ts
        self.shrines.append(make_shrine("sacrifice_altar", gx, gy - 40, "starting_area"))

        

    def region_at_tile(self, col, row):
        rid = self.tile_region.get((col, row))
        if rid:
            return rid
        for rid, rect in reversed(self.region_rects):
            if rect.collidepoint(col, row):
                return rid
        return "starting_area"

    def region_at_pixel(self, x, y):
        ts = config.TILE_SIZE
        return self.region_at_tile(int(x // ts), int(y // ts))

    def region_walkable_count(self, rid):
        for r_id, rect in self.region_rects:
            if r_id == rid:
                g = self.tilemap.grid
                return sum(1 for r in range(rect.top, rect.bottom)
                           for c in range(rect.left, rect.right) if g[r][c] == 0)
        return 0

    def spawn_loot(self, x, y, area_level=1, world_rules=None,
                   loot_rules_override=None, **item_kwargs):
        from core.difficulty import D
        from world.loot_rules import LootRules

        rid = self.region_at_pixel(x, y)
        ctx = {}
        if world_rules:
            ctx["world_rules"] = world_rules

        loot_rules = loot_rules_override or self.region_loot_rules.get(rid)

        # Difficulty now REWARDS with quality, not just punishes with quantity.
        quality = D.get("loot_quality_mult")
        if quality > 1.0:
            base = loot_rules if loot_rules else LootRules.default()
            loot_rules = LootRules(
                favored_damage_types=dict(base.favored_damage_types),
                rare_chance_multiplier=base.rare_chance_multiplier * quality,
                six_mod_chance_multiplier=base.six_mod_chance_multiplier * quality,
                guarantee_enabling_affix=base.guarantee_enabling_affix,
            )

        if loot_rules:
            ctx["loot_rules"] = loot_rules
        if rid != "starting_area":
            ctx["extension_id"] = rid

        item = generate_random_item(self.rng, area_level=area_level,
                                    world_context=ctx or None, **item_kwargs)
        self.loot_drops.append({"x": x, "y": y, "item": item})


    def spawn_pickup(self, x, y, kind):
        self.loot_drops.append({"x": x, "y": y, "item": None, "kind": kind})

    def spawn_orb(self, x, y):
        self.spawn_pickup(x, y, "heal")

    def draw(self, surface, camera, gates=None):
        self.tilemap.draw(surface, camera)     # doors are tiles now
        for drop in self.loot_drops:
            pos = camera.world_to_screen((drop["x"], drop["y"]))
            kind = drop.get("kind")
            if kind == "heal":
                pygame.draw.circle(surface, (220, 50, 70), pos, 7)
                pygame.draw.circle(surface, (255, 200, 200), pos, 7, 1)
                continue
            if kind == "stone":
                pts = [(pos[0], pos[1] - 10), (pos[0] + 8, pos[1]),
                    (pos[0], pos[1] + 10), (pos[0] - 8, pos[1])]
                pygame.draw.polygon(surface, (170, 90, 255), pts)
                pygame.draw.polygon(surface, (240, 220, 255), pts, 2)
                continue
            pygame.draw.circle(surface, drop["item"].rarity_color(), pos, 8)
            pygame.draw.circle(surface, (0, 0, 0), pos, 8, 1)



def build_level(rng_service) -> Level:
    stream = rng_service.get_stream("starting_area")
    return Level(LEVEL_SPEC, stream)