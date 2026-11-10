# [PROJECT: enemies]
import math

from enemies.enemy import Enemy


class SpawnerEnemy(Enemy):
    """Stationary damage target. Killing one advances the destroy_spawners seal."""

    def __init__(self, x, y, area_level=1):
        super().__init__(
            x, y,
            name="Enemy Spawner",
            hp=80 + area_level * 40,
            damage=0,
            armor=area_level,
            speed=0.0,
            xp_reward=5,
            area_level=area_level,
            damage_type="physical",
            behavior="chaser",
        )
        self.is_spawner = True
        self.is_elite = False
        self.width = 40
        self.height = 40
        self._aggro_range = 0

    def update(self, dt, player, tilemap, event_bus, world_rules=None):
        if not self.alive:
            return
        if self.hit_flash_timer > 0:
            self.hit_flash_timer -= dt
        if self.stagger_timer > 0:
            self.stagger_timer -= dt
        if self.slow_timer > 0:
            self.slow_timer -= dt


class ConvoyCarrier(Enemy):
    """Patrols a path between waypoints. Killing one advances hunt_convoy.
    Reaching the final waypoint escapes instead."""

    def __init__(self, x, y, path_points, area_level=1):
        super().__init__(
            x, y,
            name="Convoy Carrier",
            hp=120 + area_level * 30,
            damage=8 + area_level * 2,
            armor=area_level,
            speed=55.0,
            xp_reward=30,
            area_level=area_level,
            damage_type="physical",
            behavior="ranged",
        )
        self.is_convoy_carrier = True
        self.path_points = [tuple(p) for p in path_points] or [(x, y)]
        self.path_index = 1 if len(self.path_points) > 1 else 0
        self.width = 36
        self.height = 36
        self._aggro_range = 320
        self.reached_destination = False

    def _tick_patrol(self, dt):
        if self.reached_destination or not self.path_points:
            return
        if len(self.path_points) < 2:
            return
        target = self.path_points[self.path_index]
        dx = target[0] - self.x
        dy = target[1] - self.y
        d = math.hypot(dx, dy) or 1.0
        if d < 20:
            self.path_index += 1
            if self.path_index >= len(self.path_points):
                self.path_index = len(self.path_points) - 1
                self.reached_destination = True
            return
        speed = self.speed * (self.slow_factor if self.slow_timer > 0 else 1.0)
        self.x += dx / d * speed * dt
        self.y += dy / d * speed * dt

    def update(self, dt, player, tilemap, event_bus, world_rules=None):
        if not self.alive:
            return
        if self.hit_flash_timer > 0:
            self.hit_flash_timer -= dt
        if self.stagger_timer > 0:
            self.stagger_timer -= dt
            return
        if self.slow_timer > 0:
            self.slow_timer -= dt
        self._tick_patrol(dt)