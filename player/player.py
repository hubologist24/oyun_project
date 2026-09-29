"""
Player entity: position, movement, stats, inventory/equipment, combat.
"""
import pygame
import time
import core.config as config
from player.stats import Stats, compute_effective_stats
from player.progression import Progression
from items.inventory import Inventory

from items.equipment_slots import EQUIPMENT_SLOTS, resolve_equip_slot
from items.stash import Stash

from items.weapon_subtypes import WeaponSubtypes


class Player:
    def __init__(self, x: float, y: float, event_bus):
        self.x = x
        self.y = y
        self.width = 32
        self.height = 32
        self.facing = pygame.Vector2(0, 1)
        self.move_target: pygame.Vector2 | None = None
        self._move_target_stop_radius = 6.0

        self.base_stats = Stats()
        self.effective_stats = Stats()
        self.progression = Progression()
        self.inventory = Inventory()
        self.equipped = {slot: None for slot in EQUIPMENT_SLOTS}
        self.stash = Stash()

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
        equipped_items = [item for item in self.equipped.values() if item is not None]
        old_hp_ratio = 1.0
        if self.effective_stats.max_hp > 0:
            old_hp_ratio = self.effective_stats.hp / self.effective_stats.max_hp
        self.effective_stats = compute_effective_stats(self.base_stats, equipped_items)
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

        keyboard_active = dx != 0 or dy != 0
        if keyboard_active:
            self.move_target = None  # keyboard always overrides click-to-walk
            vec = pygame.Vector2(dx, dy)
            if vec.length_squared() > 0:
                vec = vec.normalize()
            self.facing = vec
        elif self.move_target is not None:
            to_target = self.move_target - pygame.Vector2(self.x, self.y)
            if to_target.length() <= self._move_target_stop_radius:
                self.move_target = None
                vec = pygame.Vector2(0, 0)
            else:
                vec = to_target.normalize()
                self.facing = vec
        else:
            vec = pygame.Vector2(0, 0)

        if vec.length_squared() > 0:
            speed = self.effective_stats.speed
            new_x = self.x + vec.x * speed * dt
            new_y = self.y + vec.y * speed * dt
            if not tilemap.collides_rect(self._rect_at(new_x, self.y)):
                self.x = new_x
            else:
                self.move_target = None  # blocked by wall -> stop walking to it
            if not tilemap.collides_rect(self._rect_at(self.x, new_y)):
                self.y = new_y
            else:
                self.move_target = None

        if self._attack_cooldown_timer > 0:
            self._attack_cooldown_timer -= dt
        if self._invuln_timer > 0:
            self._invuln_timer -= dt

    def face_toward(self, world_x: float, world_y: float):
        """Called by right-click-to-attack: faces the player toward the
        cursor position before the attack hitbox is computed."""
        vec = pygame.Vector2(world_x - self.x, world_y - self.y)
        if vec.length_squared() > 0:
            self.facing = vec.normalize()

    def _rect_at(self, x, y) -> pygame.Rect:
        return pygame.Rect(int(x - self.width / 2), int(y - self.height / 2),
                            self.width, self.height)

    # ---------- combat ----------
    def can_attack(self) -> bool:
        return self._attack_cooldown_timer <= 0
    

    def start_attack_cooldown(self, profile=None):
        mult = profile.attack_cooldown_multiplier if profile is not None else 1.0
        self._attack_cooldown_timer = config.PLAYER_ATTACK_COOLDOWN * mult

    def current_attack_profile(self):
        """Attack pattern is bound to the equipped weapon's subtype.
        Player attributes never alter this -- only the weapon does."""
        from combat.attack_patterns import resolve_attack_profile
        return resolve_attack_profile(self)

    def meets_requirements(self, item):
        """
        Returns (ok: bool, missing: list[str]).
        Only weapons impose requirements; all other slots are unrestricted.
        Requirements are base restrictions (per design), NOT quality gates.
        """
        subtype = getattr(item, "weapon_subtype", None)
        if not subtype:
            return True, []
        reqs = WeaponSubtypes.stat_requirements(subtype)
        missing = []
        for attr, need in reqs.items():
            have = getattr(self.effective_stats, attr, 0)
            if have < need:
                missing.append(f"{attr.title()} {need} (have {have})")
        return (len(missing) == 0), missing    

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
            self.base_stats.strength += 1
            self.base_stats.dexterity += 1
            self.base_stats.intelligence += 1
            self.event_bus.emit("level_up", level=lvl)
        if levels:
            self.recalc_stats()
            self.effective_stats.hp = self.effective_stats.max_hp

    # ---------- equipment ----------
    def equip(self, item, slot: str = None) -> bool:
        ok, _ = self.meets_requirements(item)
        if not ok:
            return False
        target_slot = slot or resolve_equip_slot(item.slot, self.equipped)
        if target_slot is None:
            return False
        old = self.equipped.get(target_slot)
        if old is not None:
            self.inventory.add_item(old)
        self.equipped[target_slot] = item
        self.inventory.remove_item(item)
        self.recalc_stats()
        self.event_bus.emit("item_equipped", item_id=item.item_id, slot=target_slot)
        return True
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

    def set_move_target(self, world_x: float, world_y: float):
        """Called by mouse left-click-to-walk. Cleared automatically on
        arrival, or instantly overridden by any WASD key press in
        handle_input() (keyboard always takes priority)."""
        self.move_target = pygame.Vector2(world_x, world_y)

    def clear_move_target(self):
        self.move_target = None
    # ---------- serialization ----------
    def to_dict(self):
        return {
            "x": self.x,
            "y": self.y,
            "base_stats": self.base_stats.to_dict(),
            "progression": self.progression.to_dict(),
            "inventory": self.inventory.to_dict(),
            "equipped": {
                slot: (item.to_dict() if item else None)
                for slot, item in self.equipped.items()
            },
        }

    @staticmethod
    def from_dict(d, event_bus):
        from items.item import Item
        from items.inventory import Inventory as InventoryCls

        # Top-level guard: a corrupted/legacy save may have stored the
        # player payload as a list (or something else). Start fresh
        # rather than crashing the entire load.
        if not isinstance(d, dict):
            d = {}

        p = Player(d.get("x", 0), d.get("y", 0), event_bus)

        base_stats_d = d.get("base_stats", {})
        p.base_stats = Stats.from_dict(base_stats_d) if isinstance(base_stats_d, dict) else Stats()

        prog_d = d.get("progression", {})
        p.progression = Progression.from_dict(prog_d) if isinstance(prog_d, dict) else Progression()

        inv_d = d.get("inventory", {})
        p.inventory = InventoryCls.from_dict(inv_d) if isinstance(inv_d, dict) else InventoryCls()

        p.equipped = {slot: None for slot in EQUIPMENT_SLOTS}
        equipped_d = d.get("equipped", {})
        if isinstance(equipped_d, dict):
            for slot, item_dict in equipped_d.items():
                if item_dict and slot in p.equipped and isinstance(item_dict, dict):
                    p.equipped[slot] = Item.from_dict(item_dict)
        elif isinstance(equipped_d, list):
            # Legacy/alternate shape: a list of {"slot": ..., "item": ...}
            for entry in equipped_d:
                if not isinstance(entry, dict):
                    continue
                slot = entry.get("slot")
                item_dict = entry.get("item")
                if slot in p.equipped and isinstance(item_dict, dict):
                    p.equipped[slot] = Item.from_dict(item_dict)

        p.recalc_stats()
        hp = d.get("current_hp", p.effective_stats.max_hp)
        if not isinstance(hp, (int, float)):
            hp = p.effective_stats.max_hp
        p.effective_stats.hp = hp
        p.effective_stats.clamp_hp()
        return p