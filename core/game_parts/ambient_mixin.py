"""Ambient world events: merchant, ambush, blessing, blood moon."""
import math
import pygame

import core.config as config


class AmbientMixin:

    def _roll_ambient_event_timer(self):
        rng = self.world.rng_service.get_stream("ambient_timer")
        lo = getattr(config, "AMBIENT_EVENT_MIN_INTERVAL", 90.0)
        hi = getattr(config, "AMBIENT_EVENT_MAX_INTERVAL", 180.0)
        return rng.uniform(lo, hi)

    def _tick_ambient_events(self, dt):
        if not getattr(config, "AMBIENT_EVENTS_ENABLED", True):
            return
        if not self.player.alive:
            return
        if (getattr(config, "AMBIENT_EVENT_SAFE_ROOM_PROTECTION", True)
                and self.starting_area.safe_room_rect.collidepoint(
                    self.player.x, self.player.y)):
            return
        self._ambient_event_timer -= dt
        if self._ambient_event_timer > 0:
            return
        self._ambient_event_timer = self._roll_ambient_event_timer()
        self._fire_ambient_event()

    def _fire_ambient_event(self):
        rng = self.world.rng_service.get_stream("ambient_events")
        events = [
            ("merchant",   self._ambient_merchant,   3),
            ("ambush",     self._ambient_ambush,     3),
            ("blessing",   self._ambient_blessing,   2),
            ("blood_moon", self._ambient_blood_moon, 1),
        ]
        weights = [e[2] for e in events]
        chosen = rng.choices(events, weights=weights, k=1)[0]
        chosen[1](rng)

    def _ambient_merchant(self, rng):
        from items.item_generator import generate_random_item
        level = max(1, self.player.progression.level)
        for _ in range(rng.randint(1, 2)):
            item = generate_random_item(rng, area_level=level, force_rarity="rare")
            angle = rng.uniform(0, math.tau)
            dist = rng.uniform(30, 70)
            self.current_area.loot_drops.append({
                "x": self.player.x + math.cos(angle) * dist,
                "y": self.player.y + math.sin(angle) * dist,
                "item": item,
            })
        self._show_message("A wandering merchant vanishes — leaving rare loot behind!",
                           duration=4.0)

    def _ambient_ambush(self, rng):
        from enemies.enemy import spawn_basic_enemy
        count = rng.randint(3, 5)
        lvl = max(1, self.player.progression.level)
        rid = self.current_area_id
        tm = self.current_area.tilemap
        spawned = 0
        for _ in range(count):
            for _try in range(20):
                angle = rng.uniform(0, math.tau)
                dist = rng.uniform(280, 460)
                x = self.player.x + math.cos(angle) * dist
                y = self.player.y + math.sin(angle) * dist
                if tm.collides_rect(pygame.Rect(int(x - 14), int(y - 14), 28, 28)):
                    continue
                e = spawn_basic_enemy(x, y, rng, area_level=lvl)
                e.region_id = rid
                e.is_elite = True
                e.max_hp = int(e.max_hp * 1.4)
                e.hp = e.max_hp
                e.name = "Ambushing " + e.name
                e._pack_checked = True
                self.current_area.enemies.append(e)
                spawned += 1
                break
        if spawned:
            self._show_message(
                f"Ambush! {spawned} elite{'s' if spawned > 1 else ''} emerge from the shadows!",
                duration=4.0)

    def _ambient_blessing(self, rng):
        self.player.heal_to_full()
        self.terrain.clear_hostile()
        self._show_message("A warm blessing washes over you. Fully healed!",
                           duration=4.0)

    def _ambient_blood_moon(self, rng):
        from enemies.enemy import spawn_basic_enemy
        count = rng.randint(6, 9)
        lvl = max(1, self.player.progression.level)
        rid = self.current_area_id
        tm = self.current_area.tilemap
        spawned = 0
        for _ in range(count):
            for _try in range(20):
                angle = rng.uniform(0, math.tau)
                dist = rng.uniform(400, 700)
                x = self.player.x + math.cos(angle) * dist
                y = self.player.y + math.sin(angle) * dist
                if tm.collides_rect(pygame.Rect(int(x - 14), int(y - 14), 28, 28)):
                    continue
                e = spawn_basic_enemy(x, y, rng, area_level=lvl)
                e.region_id = rid
                e._pack_checked = True
                self.current_area.enemies.append(e)
                spawned += 1
                break
        if spawned:
            self._show_message(
                f"BLOOD MOON!  {spawned} enemies surge from the darkness...",
                duration=4.0)
