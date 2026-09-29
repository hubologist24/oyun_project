"""
Base enemy - Phase 2: adds a resistances dict (defaults empty = pure
armor-based mitigation, same as Phase 1 behavior) so future world
rules / enemy types can define per-type resistance profiles.
"""
import pygame
import random
import core.config as config


class Enemy:
    def __init__(self, x, y, name="Enemy", hp=30, damage=8, armor=0,
                 speed=90.0, xp_reward=15, is_elite=False, area_level=1,
                 damage_type="physical", resistances=None):
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

        dx = player.x - self.x
        dy = player.y - self.y
        dist = (dx ** 2 + dy ** 2) ** 0.5

        if dist < self._aggro_range and dist > 34:
            vec = pygame.Vector2(dx, dy)
            if vec.length_squared() > 0:
                vec = vec.normalize()
            new_x = self.x + vec.x * self.speed * dt
            new_y = self.y + vec.y * self.speed * dt
            if not tilemap.collides_rect(self._rect_at(new_x, self.y)):
                self.x = new_x
            if not tilemap.collides_rect(self._rect_at(self.x, new_y)):
                self.y = new_y
        elif dist <= 34 and self._attack_timer <= 0:
            from combat.combat import enemy_attack_player
            enemy_attack_player(self, player, event_bus)
            self._attack_timer = config.ENEMY_CONTACT_DAMAGE_COOLDOWN

    def _rect_at(self, x, y):
        return pygame.Rect(int(x - self.width / 2), int(y - self.height / 2),
                            self.width, self.height)

    def draw(self, surface, camera):
        rect = camera.world_to_screen_rect(self.rect)
        color = config.COLOR_ELITE if self.is_elite else config.COLOR_ENEMY
        pygame.draw.rect(surface, color, rect, border_radius=4)
        if self.hp < self.max_hp:
            bar_w = self.width
            bar_h = 5
            bx, by = rect.x, rect.y - 10
            pygame.draw.rect(surface, config.COLOR_HP_BAR_BG, (bx, by, bar_w, bar_h))
            frac = max(0.0, self.hp / self.max_hp)
            pygame.draw.rect(surface, config.COLOR_HP_BAR_FG, (bx, by, int(bar_w * frac), bar_h))


def spawn_basic_enemy(x, y, rng, area_level=1):
    is_elite = rng.random() < 0.12
    hp = int(24 + area_level * 6 * (1.8 if is_elite else 1.0))
    dmg = int(6 + area_level * 1.6 * (1.6 if is_elite else 1.0))
    # B6: use the injected rng, not the module-global `random`.
    name = "Elite Marauder" if is_elite else rng.choice(
        ["Feral Rat", "Bandit", "Wild Wolf", "Skeleton"]
    )
    return Enemy(x, y, name=name, hp=hp, damage=dmg, armor=area_level, speed=95.0,
                 xp_reward=18 if is_elite else 10, is_elite=is_elite,
                 area_level=area_level)