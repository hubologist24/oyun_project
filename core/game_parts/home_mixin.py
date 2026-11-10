# core/game_parts/home_mixin.py
# [PROJECT: core]
# Pacified regions become "home": rest + stash shrines spawn at the region
# start, the player slowly regenerates there, ambush/blood-moon ambient
# events are suppressed (patch ambient_mixin), and a wandering merchant
# occasionally leaves a loot cache.
# No new save data: everything derives from world.cleared_regions,
# which is already persisted.
import math
import pygame

from world.shrine import make_shrine

HOME_REGEN_FRAC = 0.03          # fraction of max hp per second
HOME_MERCHANT_INTERVAL = 150.0  # seconds between merchant rolls
HOME_MERCHANT_CHANCE = 0.5


class HomeMixin:

    def _is_home_region(self, rid=None):
        rid = self.current_area_id if rid is None else rid
        return rid in self.world.cleared_regions

    # ---- spawning -------------------------------------------------------

    def _find_free_spot(self, x, y):
        tm = self.current_area.tilemap
        for radius in (0, 24, 48, 72, 96):
            for ang in range(0, 360, 45):
                nx = x + math.cos(math.radians(ang)) * radius
                ny = y + math.sin(math.radians(ang)) * radius
                r = pygame.Rect(int(nx - 14), int(ny - 14), 28, 28)
                if not tm.collides_rect(r):
                    return nx, ny
        return x, y

    def _spawn_home_shrines(self, rid):
        pos = self.current_area.region_start.get(rid)
        if not pos:
            return
        for shrine_type, dx, dy in (("rest_shrine", -70, 0),
                                    ("stash_chest", 70, 0)):
            x, y = self._find_free_spot(pos[0] + dx, pos[1] + dy)
            s = make_shrine(shrine_type, x, y, rid)
            if all(o.unique_id() != s.unique_id() for o in self.current_area.shrines):
                self.current_area.shrines.append(s)

    def _on_region_pacified_home(self, rid):
        """Call from _on_boss_killed right after cleared_regions.add(rid)."""
        self._spawn_home_shrines(rid)
        self._show_message(
            f"{self._area_display_name(rid)} is pacified. "
            f"It will shelter you now.", duration=4.0)   

        

    def _ensure_home_shrines(self):
        """Call at the end of _load_game so home shrines survive saves."""
        for rid in list(self.world.cleared_regions):
            self._spawn_home_shrines(rid)

    # ---- per-frame ------------------------------------------------------

    def _tick_home_region(self, dt):
        if not self._is_home_region() or not self.player.alive:
            return

        s = self.player.effective_stats
        if s.hp < s.max_hp:
            s.hp = min(s.max_hp, s.hp + s.max_hp * HOME_REGEN_FRAC * dt)

        t = getattr(self, "_home_merchant_timer", 0.0) - dt
        self._home_merchant_timer = t
        if t > 0:
            return
        self._home_merchant_timer = HOME_MERCHANT_INTERVAL
        self._maybe_home_merchant()

    def _maybe_home_merchant(self):
        import random
        if random.random() > HOME_MERCHANT_CHANCE:
            return
        for sh in self.current_area.shrines:
            if (sh.def_.id == "loot_chest" and not sh.used
                    and math.hypot(sh.x - self.player.x,
                                   sh.y - self.player.y) < 600):
                return
        x, y = self._find_free_spot(self.player.x + 90, self.player.y)
        self.current_area.shrines.append(
            make_shrine("loot_chest", x, y, self.current_area_id))
        self._show_message(
            "A wandering merchant left a cache in this pacified region.",
            duration=3.0)