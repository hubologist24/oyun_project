"""Region pressure: escalating ambushes and chaos hazard."""
import math
import pygame

from core.difficulty import D
from core.game_parts.constants import PRESSURE_THRESHOLDS, PRESSURE_LABELS


class PressureMixin:

    def _pressure_level(self, rid):
        t = self.world.region_presence.get(rid, 0.0)
        level = 0
        mult = D.get("pressure_time_mult")
        for i, thresh in enumerate(PRESSURE_THRESHOLDS):
            if t >= thresh * mult:
                level = i
        return level

    def _pressure_label(self, level):
        return PRESSURE_LABELS[min(level, len(PRESSURE_LABELS) - 1)]

    def _update_region_pressure(self, dt):
        rid = self.current_extension_id
        if rid is None:
            return
        ext = self.world.get_extension(rid)
        if ext is None:
            return
        boss_alive = any(b.alive and getattr(b, "region_id", None) == rid
                         for b in self.current_area.bosses)
        if not boss_alive:
            return                                  # guardian dead -> pressure frozen
        self.world.region_presence[rid] = self.world.region_presence.get(rid, 0.0) + dt
        level = self._pressure_level(rid)

        if level >= 3:
            self._pressure_ambush_timer -= dt
            if self._pressure_ambush_timer <= 0:
                self._pressure_ambush_timer = max(20.0, 45.0 - (level - 3) * 10.0)
                self._spawn_pressure_ambush(rid, ext, level)

        if level >= 4:
            self._pressure_hazard_timer -= dt
            if self._pressure_hazard_timer <= 0:
                self._pressure_hazard_timer = 8.0
                self._apply_pressure_hazard(rid)

    def _spawn_pressure_ambush(self, rid, ext, level):
        from enemies.enemy import spawn_basic_enemy
        rng = self.world.rng_service.get_stream(f"pressure_ambush:{rid}")
        count = min(4, 1 + (level - 3))
        for _ in range(count):
            for _try in range(20):
                angle = rng.uniform(0, math.tau)
                dist = rng.uniform(400, 700)
                x = self.player.x + math.cos(angle) * dist
                y = self.player.y + math.sin(angle) * dist
                if self.current_area.tilemap.collides_rect(
                        pygame.Rect(int(x - 14), int(y - 14), 28, 28)):
                    continue
                e = spawn_basic_enemy(x, y, rng, area_level=ext.spec.area_level)
                e.region_id = rid
                e.is_elite = True
                e.max_hp = int(e.max_hp * 1.5)
                e.hp = e.max_hp
                e.name = "Ambushing " + e.name
                self.current_area.enemies.append(e)
                break
        self._show_message(
            f"The region's pressure draws an ambush! [{self._pressure_label(level)}]",
            duration=2.5)

    def _apply_pressure_hazard(self, rid):
        if not self.player.alive:
            return
        from combat.damage import resolve_damage
        stats = self.player.effective_stats
        base = max(3, int(stats.max_hp * 0.03))
        res = dict(stats.resistances)
        res["armor_flat"] = stats.armor
        dmg = resolve_damage(base, "chaos", None, target_resistances=res,
                             world_rules=self._active_world_rules(),
                             target_category="player", source_category="elite")
        if self.player.take_damage(dmg, cause="chaos"):
            self.damage_numbers.spawn(self.player.x, self.player.y - 30,
                                      str(dmg), (200, 90, 255))
            self.event_bus.emit("player_hit", source="the corrupted world",
                                damage=dmg, damage_type="chaos")
