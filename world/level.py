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
    },
    "enemy_counts": {"arena_a": 4, "arena_b": 5, "arena_c": 5},
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

        mid_y = (sy0 + sy1) // 2
        _carve_corridor_h(grid, sx1 - 1, ax0 + 1, mid_y)
        _carve_corridor_h(grid, ax1 - 1, bx0 + 1, mid_y)

        mid_x = (sx0 + sx1) // 2
        _carve_corridor_v(grid, sy1 - 1, cy0 + 1, mid_x)

        bx_mid = (bx0 + bx1) // 2
        _carve_corridor_v(grid, by1 - 1, bossy0 + 1, bx_mid)

        self.tilemap = TileMap(grid)
        self.enemies = []
        self.loot_drops = []

        self._spawn_enemies()
        self.boss = Boss(*self._room_center(rooms["boss_room"]), BossTemplate.hollow_warden())
        self.start_pos = self._room_center(rooms["start"])

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

    def spawn_loot(self, x, y, area_level=1, world_rules=None):
        item = generate_random_item(self.rng, area_level=area_level,
                                     world_context={"world_rules": world_rules} if world_rules else None)
        self.loot_drops.append({"x": x, "y": y, "item": item})

    def draw(self, surface, camera):
        self.tilemap.draw(surface, camera)
        for drop in self.loot_drops:
            pos = camera.world_to_screen((drop["x"], drop["y"]))
            pygame.draw.circle(surface, drop["item"].rarity_color(), pos, 8)
            pygame.draw.circle(surface, (0, 0, 0), pos, 8, 1)


class AreaInstance:
    """
    Runtime playable area built from an Extension's procedural map data.
    Phase 7: boss is now personalized via enemies/boss_factory.py, and
    loot generation is biased via the extension's LootRules.
    """

    def __init__(self, area_data: dict, rng_stream, extension):
        self.tilemap = area_data["tilemap"]
        self.rooms = area_data["rooms"]
        self.area_level = area_data["area_level"]
        self.extension = extension
        self.rng = rng_stream

        self.enemies = []
        self.loot_drops = []

        ts = config.TILE_SIZE
        start_room = area_data["start_room"]
        self.start_pos = self._room_center(start_room, ts)

        for room in area_data["enemy_rooms"]:
            count = rng_stream.randint(2, 4)
            for _ in range(count):
                x0, y0, x1, y1 = room
                col = rng_stream.randint(x0 + 1, max(x0 + 1, x1 - 2))
                row = rng_stream.randint(y0 + 1, max(y0 + 1, y1 - 2))
                x, y = col * ts + ts / 2, row * ts + ts / 2
                self.enemies.append(spawn_basic_enemy(x, y, rng_stream, area_level=self.area_level))

        self.boss = None
        if area_data.get("boss_room") and extension.spec.boss_template_id:
            from enemies.boss_factory import build_personalized_boss_template
            from ai.validators import BossValidator

            bx, by = self._room_center(area_data["boss_room"], ts)
            boss_name = f"Guardian of {extension.spec.extension_name}"
            template = build_personalized_boss_template(
                boss_name, extension.spec.boss_personalization, self.area_level, rng_stream
            )

            # Even procedurally-built templates pass through BossValidator
            # (Phase 5) -- personalization must never bypass safety bounds.
            boss_dict = {
                "max_hp": template.max_hp,
                "phases": template.phases,
            }
            valid, errors = BossValidator.validate(boss_dict)
            if not valid:
                print(f"[AreaInstance] Personalized boss failed BossValidator {errors}, "
                      f"using safe default template.")
                template = BossTemplate.hollow_warden()
                template.name = boss_name

            self.boss = Boss(bx, by, template)

    def _room_center(self, room, ts):
        x0, y0, x1, y1 = room
        return ((x0 + x1) / 2 * ts, (y0 + y1) / 2 * ts)

    def spawn_loot(self, x, y, area_level=None, world_rules=None):
        lvl = area_level if area_level is not None else self.area_level
        loot_rules = None
        if hasattr(self.extension, "spec") and self.extension.spec.loot_rules:
            from world.loot_rules import LootRules
            loot_rules = LootRules.from_dict(self.extension.spec.loot_rules)

        item = generate_random_item(
            self.rng, area_level=lvl,
            world_context={"world_rules": world_rules, "loot_rules": loot_rules} if world_rules or loot_rules else None,
        )
        self.loot_drops.append({"x": x, "y": y, "item": item})

    def draw(self, surface, camera):
        self.tilemap.draw(surface, camera)
        for drop in self.loot_drops:
            pos = camera.world_to_screen((drop["x"], drop["y"]))
            pygame.draw.circle(surface, drop["item"].rarity_color(), pos, 8)
            pygame.draw.circle(surface, (0, 0, 0), pos, 8, 1)


def build_level(rng_service) -> Level:
    stream = rng_service.get_stream("starting_area")
    return Level(LEVEL_SPEC, stream)