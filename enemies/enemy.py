"""
Base enemy - Phase 2: adds a resistances dict (defaults empty = pure
armor-based mitigation, same as Phase 1 behavior) so future world
rules / enemy types can define per-type resistance profiles.
"""
import pygame
import random
import core.config as config
import math


class Enemy:
    def __init__(self, x, y, name="Enemy", hp=30, damage=8, armor=0,
                 speed=90.0, xp_reward=15, is_elite=False, area_level=1,
                 damage_type="physical", resistances=None  ,behavior="chaser"):
        self.x = x
        self.y = y
        self.width = 28
        self.height = 28
        self.name = name
        self.max_hp = hp
        self.hp = hp
        self.damage = damage
        self.armor = armor
        self.speed = speed
        self.xp_reward = xp_reward
        self.is_elite = is_elite
        self.area_level = area_level
        self.damage_type = damage_type
        self.resistances = dict(resistances) if resistances else {}
        self.resistances.setdefault("armor_flat", armor)

        self.behavior = behavior
        self._strafe_dir = 1 if random.random() < 0.5 else -1
        self._strafe_timer = 0.0
        self._ranged_timer = 1.2
        self.projectiles = []

        self.alive = True
        self._attack_timer = 0.0
        self._aggro_range = 260

    @property
    def rect(self) -> pygame.Rect:
        return pygame.Rect(int(self.x - self.width / 2), int(self.y - self.height / 2),
                            self.width, self.height)

    def take_damage(self, amount: int):
        self.hp -= amount
        if self.hp <= 0:
            self.hp = 0
            self.alive = False

    def update(self, dt, player, tilemap, event_bus):
        if not self.alive:
            return
        if self._attack_timer > 0:
            self._attack_timer -= dt
        if self._ranged_timer > 0:
            self._ranged_timer -= dt
        self._strafe_timer -= dt
        if self._strafe_timer <= 0:
            self._strafe_dir *= -1
            self._strafe_timer = random.uniform(0.8, 2.2)

        dx = player.x - self.x
        dy = player.y - self.y
        dist = math.hypot(dx, dy)

        handler = {
            "chaser":   self._tick_chaser,
            "ambusher": self._tick_ambusher,
            "circler":  self._tick_circler,
            "ranged":   self._tick_ranged,
        }.get(self.behavior, self._tick_chaser)
        handler(dt, player, tilemap, dist, dx, dy, event_bus)

        self._tick_projectiles(dt, player, event_bus)

    def _rect_at(self, x, y):
        return pygame.Rect(int(x - self.width / 2), int(y - self.height / 2),
                            self.width, self.height)

    def draw(self, surface, camera):
        rect = camera.world_to_screen_rect(self.rect)
        base = config.COLOR_ELITE if self.is_elite else config.COLOR_ENEMY
        tint = {"chaser": (0, 0, 0), "ambusher": (40, 0, 40),
                "circler": (0, 30, 40), "ranged": (40, 20, 0)}.get(self.behavior, (0, 0, 0))
        color = tuple(min(255, c + t) for c, t in zip(base, tint))
        pygame.draw.rect(surface, color, rect, border_radius=4)

        if self.hp < self.max_hp:
            bar_w, bar_h = self.width, 5
            bx, by = rect.x, rect.y - 10
            pygame.draw.rect(surface, config.COLOR_HP_BAR_BG, (bx, by, bar_w, bar_h))
            frac = max(0.0, self.hp / self.max_hp)
            pygame.draw.rect(surface, config.COLOR_HP_BAR_FG,
                            (bx, by, int(bar_w * frac), bar_h))

        for proj in self.projectiles:
            p = camera.world_to_screen((proj["x"], proj["y"]))
            pygame.draw.circle(surface, (255, 160, 80), p, 5)
            pygame.draw.circle(surface, (255, 255, 255), p, 5, 1)

    def _step_toward(self, dt, tilemap, dir_x, dir_y, speed_scale=1.0):
        v = pygame.Vector2(dir_x, dir_y)
        if v.length_squared() == 0:
            return
        v = v.normalize()
        nx = self.x + v.x * self.speed * speed_scale * dt
        ny = self.y + v.y * self.speed * speed_scale * dt
        if not tilemap.collides_rect(self._rect_at(nx, self.y)):
            self.x = nx
        if not tilemap.collides_rect(self._rect_at(self.x, ny)):
            self.y = ny


    def _contact_attack(self, player, event_bus):
        if self._attack_timer > 0:
            return
        from combat.combat import enemy_attack_player
        enemy_attack_player(self, player, event_bus)
        self._attack_timer = config.ENEMY_CONTACT_DAMAGE_COOLDOWN


    def _tick_chaser(self, dt, player, tilemap, dist, dx, dy, event_bus):
        if dist < self._aggro_range and dist > 34:
            self._step_toward(dt, tilemap, dx, dy, 1.0)
        elif dist <= 34:
            self._contact_attack(player, event_bus)


    def _tick_ambusher(self, dt, player, tilemap, dist, dx, dy, event_bus):
        # Dormant until the player commits to the room, then lunges fast.
        if dist > 220:
            return
        if dist > 60:
            self._step_toward(dt, tilemap, dx, dy, 0.4)   # creep
        elif dist > 34:
            self._step_toward(dt, tilemap, dx, dy, 2.2)   # lunge
        else:
            self._contact_attack(player, event_bus)


    def _tick_circler(self, dt, player, tilemap, dist, dx, dy, event_bus):
        # Prefers mid-range; strafes to stay there.
        if dist > self._aggro_range:
            return
        if dist > 160:
            self._step_toward(dt, tilemap, dx, dy, 1.0)
        elif dist < 90:
            self._step_toward(dt, tilemap, -dx, -dy, 0.9)
        else:
            perp_x, perp_y = -dy, dx
            self._step_toward(dt, tilemap, perp_x * self._strafe_dir,
                            perp_y * self._strafe_dir, 1.0)
        if dist <= 40:
            self._contact_attack(player, event_bus)


    def _tick_ranged(self, dt, player, tilemap, dist, dx, dy, event_bus):
        # Kites: backs off under 160, closes past 300, else strafes and fires.
        if dist > self._aggro_range + 60:
            return
        if dist < 160:
            self._step_toward(dt, tilemap, -dx, -dy, 1.1)
        elif dist > 300:
            self._step_toward(dt, tilemap, dx, dy, 0.8)
        else:
            perp_x, perp_y = -dy, dx
            self._step_toward(dt, tilemap, perp_x * self._strafe_dir,
                            perp_y * self._strafe_dir, 0.9)
        if dist <= 380 and self._ranged_timer <= 0:
            self._fire_bolt(dx, dy, dist)
            self._ranged_timer = 1.6


    def _fire_bolt(self, dx, dy, dist):
        if dist < 1:
            return
        speed = 260.0
        self.projectiles.append({
            "x": self.x, "y": self.y,
            "vx": dx / dist * speed,
            "vy": dy / dist * speed,
            "damage": self.damage,
            "life": 3.0,
        })


    def _tick_projectiles(self, dt, player, event_bus):
        from combat.damage import resolve_damage
        for proj in list(self.projectiles):
            proj["x"] += proj["vx"] * dt
            proj["y"] += proj["vy"] * dt
            proj["life"] -= dt
            if proj["life"] <= 0:
                self.projectiles.remove(proj)
                continue
            if math.hypot(proj["x"] - player.x, proj["y"] - player.y) < 18:
                stats = player.effective_stats
                target_res = dict(stats.resistances)
                target_res["armor_flat"] = stats.armor
                dmg = resolve_damage(proj["damage"], self.damage_type, None,
                                    target_resistances=target_res,
                                    target_category="player")
                if player.take_damage(dmg, cause=self.damage_type):
                    event_bus.emit("player_hit", source=self.name,
                                damage=dmg, damage_type=self.damage_type)
                self.projectiles.remove(proj)

BEHAVIOR_TABLE = [
    ("chaser",   50, 1.0, 1.0, 95.0),
    ("ambusher", 20, 0.9, 1.2, 110.0),
    ("circler",  20, 1.0, 0.9, 105.0),
    ("ranged",   10, 0.8, 0.8, 85.0),
]
NAME_POOL = {
    "chaser":   ["Feral Rat", "Bandit", "Wild Wolf", "Skeleton"],
    "ambusher": ["Lurker", "Cave Spider", "Stalker"],
    "circler":  ["Wisp", "Dust Shade", "Slinker"],
    "ranged":   ["Cultist", "Bone Archer", "Hexer"],
}


def spawn_basic_enemy(x, y, rng, area_level=1):
    is_elite = rng.random() < 0.12
    behavior, _, hp_mult, dmg_mult, speed = rng.choices(
        BEHAVIOR_TABLE,
        weights=[w for _, w, *_ in BEHAVIOR_TABLE],
        k=1,
    )[0]

    hp = int((24 + area_level * 6) * hp_mult * (1.8 if is_elite else 1.0))
    dmg = int((6 + area_level * 1.6) * dmg_mult * (1.6 if is_elite else 1.0))
    name = (f"Elite {behavior.title()}" if is_elite
            else rng.choice(NAME_POOL[behavior]))

    return Enemy(x, y, name=name, hp=hp, damage=dmg, armor=area_level,
                 speed=speed, xp_reward=18 if is_elite else 10,
                 is_elite=is_elite, area_level=area_level, behavior=behavior)

