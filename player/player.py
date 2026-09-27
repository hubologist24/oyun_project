"""
Player entity: position, movement, stats, inventory/equipment, combat.
"""
import pygame
import time
import core.config as config
from player.stats import Stats, compute_effective_stats
from player.progression import Progression
from items.inventory import Inventory


class Player:
    def __init__(self, x: float, y: float, event_bus):
        self.x = x
        self.y = y
        self.width = 32
        self.height = 32
        self.facing = pygame.Vector2(0, 1)

        self.base_stats = Stats()
        self.effective_stats = Stats()
        self.progression = Progression()
        self.inventory = Inventory()
        self.equipped = {"weapon": None, "armor": None}

        self.event_bus = event_bus

        self._attack_cooldown_timer = 0.0
        self._invuln_timer = 0.0
        self._last_hit_flash = 0.0

        self.alive = True
        self.recalc_stats()

    # ---------- geometry ----------
    @property
    def rect(self) -> pygame.Rect:
        return pygame.Rect(int(self.x - self.width / 2), int(self.y - self.height / 2),
                            self.width, self.height)

    # ---------- stats ----------
    def recalc_stats(self):
        equipped_items = [self.equipped.get("weapon"), self.equipped.get("armor")]
        old_hp_ratio = 1.0
        if self.effective_stats.max_hp > 0:
            old_hp_ratio = self.effective_stats.hp / self.effective_stats.max_hp
        self.effective_stats = compute_effective_stats(self.base_stats, equipped_items)
        # preserve hp ratio across recalcs (e.g. equip changes max_hp)
        self.effective_stats.hp = int(self.effective_stats.max_hp * old_hp_ratio)
        self.effective_stats.clamp_hp()

    # ---------- movement ----------
    def handle_input(self, dt: float, keys, tilemap):
        dx, dy = 0.0, 0.0
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            dy -= 1
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            dy += 1
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            dx -= 1
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            dx += 1

        if dx != 0 or dy != 0:
            vec = pygame.Vector2(dx, dy)
            if vec.length_squared() > 0:
                vec = vec.normalize()
            self.facing = vec
            speed = self.effective_stats.speed
            new_x = self.x + vec.x * speed * dt
            new_y = self.y + vec.y * speed * dt

            # Resolve collisions axis-by-axis against the tilemap
            if not tilemap.collides_rect(self._rect_at(new_x, self.y)):
                self.x = new_x
            if not tilemap.collides_rect(self._rect_at(self.x, new_y)):
                self.y = new_y

        if self._attack_cooldown_timer > 0:
            self._attack_cooldown_timer -= dt
        if self._invuln_timer > 0:
            self._invuln_timer -= dt

    def _rect_at(self, x, y) -> pygame.Rect:
        return pygame.Rect(int(x - self.width / 2), int(y - self.height / 2),
                            self.width, self.height)

    # ---------- combat ----------
    def can_attack(self) -> bool:
        return self._attack_cooldown_timer <= 0

    def start_attack_cooldown(self):
        self._attack_cooldown_timer = config.PLAYER_ATTACK_COOLDOWN

    def attack_hitbox(self) -> pygame.Rect:
        reach = config.PLAYER_ATTACK_RANGE
        cx = self.x + self.facing.x * reach * 0.5
        cy = self.y + self.facing.y * reach * 0.5
        size = reach
        return pygame.Rect(int(cx - size / 2), int(cy - size / 2), size, size)

    def take_damage(self, amount: int, cause: str = "physical") -> bool:
        """Returns True if damage was actually applied (not invulnerable)."""
        if self._invuln_timer > 0:
            return False
        self.effective_stats.hp -= amount
        self.effective_stats.clamp_hp()
        self._invuln_timer = config.PLAYER_INVULN_ON_HIT
        self._last_hit_flash = 0.15
        if self.effective_stats.hp <= 0:
            self.alive = False
            self.event_bus.emit("player_death", cause=cause)
        return True

    def heal_to_full(self):
        self.effective_stats.hp = self.effective_stats.max_hp

    def gain_xp(self, amount: int):
        levels = self.progression.add_xp(amount)
        for lvl in levels:
            # Simple growth per level; Phase 2 will tie this to a build system
            self.base_stats.max_hp += 12
            self.base_stats.base_damage += 2
            self.base_stats.armor += 1
            self.event_bus.emit("level_up", level=lvl)
        if levels:
            self.recalc_stats()
            self.effective_stats.hp = self.effective_stats.max_hp

    # ---------- equipment ----------
    def equip(self, item, slot: str):
        old = self.equipped.get(slot)
        if old is not None:
            self.inventory.add_item(old)
        self.equipped[slot] = item
        self.inventory.remove_item(item)
        self.recalc_stats()
        self.event_bus.emit("item_equipped", item_id=item.item_id, slot=slot)

    # ---------- render ----------
    def draw(self, surface, camera):
        rect = camera.world_to_screen_rect(self.rect)
        color = config.COLOR_PLAYER
        if self._invuln_timer > 0 and int(self._invuln_timer * 20) % 2 == 0:
            color = (255, 255, 255)
        pygame.draw.rect(surface, color, rect, border_radius=6)
        # facing indicator
        center = camera.world_to_screen((self.x, self.y))
        tip = camera.world_to_screen((self.x + self.facing.x * 20, self.y + self.facing.y * 20))
        pygame.draw.line(surface, (255, 255, 255), center, tip, 3)

    # ---------- serialization ----------
    def to_dict(self):
        return {
            "x": self.x,
            "y": self.y,
            "base_stats": self.base_stats.to_dict(),
            "progression": self.progression.to_dict(),
            "inventory": [item.to_dict() for item in self.inventory.items],
            "equipped": {
                slot: (item.to_dict() if item else None)
                for slot, item in self.equipped.items()
            },
        }

    @staticmethod
    def from_dict(d, event_bus):
        from items.item import Item
        p = Player(d.get("x", 0), d.get("y", 0), event_bus)
        p.base_stats = Stats.from_dict(d.get("base_stats", {}))
        p.progression = Progression.from_dict(d.get("progression", {}))
        p.inventory.items = [Item.from_dict(i) for i in d.get("inventory", [])]
        equipped = d.get("equipped", {})
        p.equipped = {
            "weapon": Item.from_dict(equipped["weapon"]) if equipped.get("weapon") else None,
            "armor": Item.from_dict(equipped["armor"]) if equipped.get("armor") else None,
        }
        p.recalc_stats()
        p.effective_stats.hp = d.get("current_hp", p.effective_stats.max_hp)
        p.effective_stats.clamp_hp()
        return p