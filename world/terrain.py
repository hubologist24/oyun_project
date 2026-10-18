"""
Reactive terrain: temporary surfaces left behind by elemental reactions.

Deliberately NOT tile mutation. Surfaces are circles with a lifetime, so
there is nothing to persist, migrate, or softlock. Surfaces interact:

  water  + lightning -> static  (shocks everything standing in it)
  water  + cold      -> ice     (freezes the target, staggers the rest)
  ice    + fire      -> water   (melts)
  blight + fire      -> fire    (ignites; hurts the player too)
  enemy dying on blight may bloom into a heal orb or imps

Hostile-to-player surfaces ("fire", "rot") have a flashing telegraph
(`arm`) before they start dealing damage.
"""
import math
import pygame
import core.config as config
from combat.damage import resolve_damage

KINDS = {
    "water":  {"color": (70, 130, 230),  "arm": 0.0, "name": "Meltwater"},
    "ice":    {"color": (170, 225, 255), "arm": 0.0, "name": "Ice"},
    "static": {"color": (255, 240, 120), "arm": 0.0, "name": "Static"},
    "blight": {"color": (150, 60, 200),  "arm": 0.0, "name": "Blight"},
    "fire":   {"color": (255, 120, 40),  "arm": 0.6, "name": "Burning Ground"},
    "rot":    {"color": (120, 170, 60),  "arm": 0.8, "name": "Corpse Rot"},
}
MAX_SURFACES = 14
TICK = 0.25


class TerrainSystem:
    def __init__(self, game):
        self.game = game
        self._fx = None
        self.reset()

    def reset(self):
        self.surfaces = []
        self.clock = 0.0

    def clear_hostile(self):
        self.surfaces = [s for s in self.surfaces if s["kind"] not in ("fire", "rot")]

    # ---------------- creation / queries ----------------
    def add_surface(self, x, y, radius, kind, duration, power=6):
        for s in self.surfaces:
            if s["kind"] == kind and math.hypot(s["x"] - x, s["y"] - y) < radius * 0.5:
                s["left"] = max(s["left"], duration)
                s["power"] = max(s["power"], power)
                return s
        s = {"x": x, "y": y, "r": radius, "kind": kind, "left": duration,
             "power": max(4, power), "arm": KINDS[kind]["arm"],
             "acc": 0.0, "n": 0, "last_conv": -99.0}
        self.surfaces.append(s)
        if len(self.surfaces) > MAX_SURFACES:
            self.surfaces.pop(0)
        return s

    def surfaces_at(self, x, y):
        return [s for s in self.surfaces
                if math.hypot(x - s["x"], y - s["y"]) <= s["r"]]

    def _convert(self, s, kind, duration, power):
        s["kind"] = kind
        s["left"] = duration
        s["power"] = max(s["power"], power)
        s["arm"] = KINDS[kind]["arm"]
        s["last_conv"] = self.clock

    def _text(self, x, y, text, kind):
        self.game.damage_numbers.spawn(x, y - 58, text, KINDS[kind]["color"])

    # ---------------- interactions ----------------
    def on_element_hit(self, target, types, hit):
        """Called by ReactionSystem.on_hit with the elements just applied."""
        r = self.game.reactions
        hit = max(4, hit)
        for s in self.surfaces_at(target.x, target.y):
            if self.clock - s["last_conv"] < 0.4:
                continue
            k = s["kind"]
            if k == "water" and "lightning" in types:
                self._convert(s, "static", 5.0, hit)
                for o in r._within(s["x"], s["y"], s["r"]):
                    r._hurt(o, hit * 0.8, "lightning", KINDS["static"]["color"])
                self._text(target.x, target.y, "ELECTRIFIED", "static")
            elif k == "water" and "cold" in types:
                self._convert(s, "ice", 6.0, hit)
                for o in r._within(s["x"], s["y"], s["r"]):
                    if getattr(o, "is_boss", False) or not hasattr(o, "apply_stagger"):
                        continue
                    if o is target:
                        o.stagger_timer = max(o.stagger_timer, 1.5)
                        r._frozen[id(o)] = {"target": o, "left": 1.5}   # next hit shatters
                    else:
                        o.apply_stagger(1.0)
                self._text(target.x, target.y, "FROZEN IN PLACE", "ice")
            elif k == "ice" and "fire" in types:
                self._convert(s, "water", 6.0, hit)
                self._text(target.x, target.y, "MELT", "water")
            elif k == "blight" and "fire" in types:
                self._convert(s, "fire", 5.0, hit)
                self._text(target.x, target.y, "IGNITED", "fire")

    def on_enemy_killed(self, enemy):
        if getattr(enemy, "is_add", False):
            return
        if not any(s["kind"] == "blight" for s in self.surfaces_at(enemy.x, enemy.y)):
            return
        g = self.game
        rng = g.world.rng_service.get_stream("terrain")
        roll = rng.random()
        if roll < 0.35:
            g.current_area.spawn_orb(enemy.x, enemy.y)
            self._text(enemy.x, enemy.y, "CORPSE BLOOM", "blight")
        elif roll < 0.50:
            from enemies.enemy import spawn_basic_enemy
            for _ in range(2):
                e = spawn_basic_enemy(enemy.x, enemy.y, rng, area_level=enemy.area_level,
                                      force_behavior="swarmer")
                e.region_id = getattr(enemy, "region_id", "starting_area")
                e.is_add = True
                e.xp_reward = 2
                e._pack_checked = True
                g.current_area.enemies.append(e)
            self._text(enemy.x, enemy.y, "CORPSE BURST", "blight")

    # ---------------- per-frame ----------------
    def update(self, dt):
        self.clock += dt
        for s in list(self.surfaces):
            s["left"] -= dt
            if s["left"] <= 0:
                if s in self.surfaces:
                    self.surfaces.remove(s)
                continue
            s["arm"] = max(0.0, s["arm"] - dt)
            s["acc"] += dt
            if s["acc"] < TICK:
                continue
            s["acc"] -= TICK
            s["n"] += 1
            self._tick(s)

    def _tick(self, s):
        if s["arm"] > 0:
            return
        g = self.game
        r = g.reactions
        k = s["kind"]
        half = (s["n"] % 2 == 0)             # every 0.5s
        color = KINDS[k]["color"]
        if k == "ice":
            for o in r._within(s["x"], s["y"], s["r"]):
                if hasattr(o, "apply_slow"):
                    o.apply_slow(0.4, 0.5)
        elif k == "static" and half:
            for o in r._within(s["x"], s["y"], s["r"]):
                r._hurt(o, s["power"] * 0.35, "lightning", color)
                if o.alive:
                    r._apply_aura(o, "lightning", s["power"])
        elif k == "blight" and half:
            for o in r._within(s["x"], s["y"], s["r"]):
                r._hurt(o, s["power"] * 0.25, "chaos", color)
        elif k == "fire" and half:
            for o in r._within(s["x"], s["y"], s["r"]):
                r._hurt(o, s["power"] * 0.3, "fire", color)
            self._hurt_player(s, "fire")
        elif k == "rot" and half:
            self._hurt_player(s, "chaos")

    def _hurt_player(self, s, dtype):
        g = self.game
        p = g.player
        if not p.alive or math.hypot(p.x - s["x"], p.y - s["y"]) > s["r"]:
            return
        stats = p.effective_stats
        res = dict(stats.resistances)
        res["armor_flat"] = stats.armor
        base = max(3, int(stats.max_hp * 0.04))
        dmg = resolve_damage(base, dtype, None, target_resistances=res,
                             world_rules=g._active_world_rules(),
                             target_category="player", source_category="elite")
        if p.take_damage(dmg, cause=dtype):
            g.damage_numbers.spawn(p.x, p.y - 30, str(dmg), KINDS[s["kind"]]["color"])

    # ---------------- drawing ----------------
    def draw(self, surface, camera):
        if not self.surfaces:
            return
        if self._fx is None:
            self._fx = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT), pygame.SRCALPHA)
        self._fx.fill((0, 0, 0, 0))
        for s in self.surfaces:
            sx, sy = camera.world_to_screen((s["x"], s["y"]))
            if (sx < -s["r"] or sy < -s["r"] or sx > config.SCREEN_WIDTH + s["r"]
                    or sy > config.SCREEN_HEIGHT + s["r"]):
                continue
            c = KINDS[s["kind"]]["color"]
            fade = min(1.0, s["left"] / 1.0)
            armed = s["arm"] <= 0
            fill_a = int((70 if armed else 28) * fade)
            ring_a = int(200 * fade) if armed else int(60 + 140 * abs(math.sin(self.clock * 14)))
            pygame.draw.circle(self._fx, (*c, fill_a), (sx, sy), int(s["r"]))
            pygame.draw.circle(self._fx, (*c, ring_a), (sx, sy), int(s["r"]), 2)
        surface.blit(self._fx, (0, 0))