"""
Player entity: position, movement, stats, inventory/equipment, combat.
"""
import pygame
import time
import core.config as config
from items import item
from player.stats import Stats, compute_effective_stats
from player.progression import Progression
from items.inventory import Inventory

from items.equipment_slots import EQUIPMENT_SLOTS, resolve_equip_slot,ITEM_SLOT_TO_EQUIP_SLOTS
from items.stash import Stash

from items.weapon_subtypes import WeaponSubtypes

from player.perks import apply_perks_to_stats, KEYSTONES
from combat.damage import ConversionRule

from combat.diminishing import rating_to_int


class Player:
    def __init__(self, x: float, y: float, event_bus,codex_manager=None):
        self.x = x
        self.y = y
        self.width = 32
        self.height = 32
        self.facing = pygame.Vector2(0, 1)
        self.move_target: pygame.Vector2 | None = None
        self._move_target_stop_radius = 6.0

        self.codex_manager = codex_manager      # NEW

        self.extension_stones = 3

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
        
        # --- DASH SYSTEM (UPDATED) ---
        self._dash_timer = 0.0
        self._dash_vec = pygame.Vector2(0, 0)
        self._dash_charges = 2
        self._dash_max_charges = 2
        self._dash_recharge_timer = 0.0
        self._dash_recharge_rate = 1.5      # seconds per charge
        self._dash_glance_timer = 0.0       # for near_miss near-dash window

        # --- STATUS EFFECTS (NEW) ---
        self._burns = []                    # [{dtype, dps, left, acc}]
        self.slow_timer = 0.0
        self.slow_factor = 1.0
        self.stagger_timer = 0.0
        self._stagger_icd = 0.0

        self.combo_count = 0
        self.combo_timer = 0.0

        self.guaranteed_crit_timer = 0.0

        self.perks = {}          # perk_id -> stack count
        self.keystones = []      # chosen keystone ids

        self.weapon_mastery = {}

        self.alive = True
        self.trophies = []
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

        for t in self.trophies[:10]:
            dt_ = t.get("damage_type") if isinstance(t, dict) else None
            if dt_:
                self.effective_stats.resistances[dt_] = \
                    self.effective_stats.resistances.get(dt_, 0.0) + 0.03
            self.effective_stats.max_hp += 5

        if self.codex_manager is not None:
            self.codex_manager.apply_bonuses_to_stats(self.effective_stats)

        apply_perks_to_stats(self, self.effective_stats)

        ks = self.active_keystones()
        self._dash_max_charges = 3 if "phantom_dash" in ks else 2
        self._dash_recharge_rate = 1.125 if "phantom_dash" in ks else 1.5
        self._dash_charges = min(self._dash_charges, self._dash_max_charges)

        self.effective_stats.hp = int(self.effective_stats.max_hp * old_hp_ratio)

        for subtype, tier in self.weapon_mastery.items():
            if tier <= 0:
                continue
            if subtype == "sword":
                self.effective_stats.crit_chance += 0.02 * tier
            elif subtype == "greatsword":
                self.effective_stats.armor += 5 * tier
            elif subtype == "bow":
                self.effective_stats.attack_speed_bonus += 0.04 * tier
            elif subtype in ("short_wand", "long_wand"):
                for d in ("fire", "cold", "lightning", "chaos"):
                    self.effective_stats.resistances[d] = \
                        self.effective_stats.resistances.get(d, 0.0) + 0.02 * tier

        self.effective_stats.clamp_hp()


    def _ranged_weapon(self):
        from items.modifiers import ALL_RANGED_SUBTYPES
        weapon = self.equipped.get("weapon")
        if weapon is None:
            return None
        if getattr(weapon, "weapon_subtype", None) not in ALL_RANGED_SUBTYPES:
            return None
        return weapon


    

    def _melee_small_weapon(self):
        from items.modifiers import MELEE_SMALL_SUBTYPES
        weapon = self.equipped.get("weapon")
        if weapon is None:
            return None
        if getattr(weapon, "weapon_subtype", None) not in MELEE_SMALL_SUBTYPES:
            return None
        return weapon

    def _sum_other_bonus_from_equipped(self, modifier_id, primary_weapon):
        total = 0.0
        if primary_weapon is not None:
            total += primary_weapon.other_bonus(modifier_id)
        for slot in ("ring_1", "ring_2", "amulet", "offhand"):
            it = self.equipped.get(slot)
            if it is not None:
                total += it.other_bonus(modifier_id)
        return total

    
    def ranged_ricochet_bonus(self) -> int:
        w = self._ranged_weapon()
        if w is None:
            return 0
        rating = self._sum_other_bonus_from_equipped("ricochet_count", w)
        if self.has_keystone("trick_shot"):
            rating += 2
        rating += getattr(self, "weapon_mastery", {}).get("gun", 0)
        return rating_to_int("ricochet_count", rating)

    def ranged_fork_bonus(self) -> int:
        w = self._ranged_weapon()
        if w is None:
            return 0
        rating = self._sum_other_bonus_from_equipped("fork_count", w)
        rating += self.perks.get("forking_shot", 0)
        if self.has_keystone("eldritch_barrage"):
            rating += 1
        return rating_to_int("fork_count", rating)

    def ranged_pierce_bonus(self) -> int:
        w = self._ranged_weapon()
        if w is None:
            return 0
        rating = self._sum_other_bonus_from_equipped("pierce_count", w)
        rating += self.perks.get("piercing_eye", 0)
        if self.has_keystone("eldritch_barrage"):
            rating += 1
        return rating_to_int("pierce_count", rating)

    def ranged_chain_bonus(self) -> int:
        w = self._ranged_weapon()
        if w is None:
            return 0
        rating = self._sum_other_bonus_from_equipped("chain_count", w)
        rating += self.perks.get("linked_aim", 0)
        if self.has_keystone("eldritch_barrage"):
            rating += 1
        return rating_to_int("chain_count", rating)
    

    

    def melee_proc_chance(self, proc_id: str) -> float:
        """Returns probability (0.0 - 1.0) for a melee-small proc."""
        w = self._melee_small_weapon()
        if w is None:
            return 0.0
        raw = self._sum_other_bonus_from_equipped(proc_id, w)
        if self.has_keystone("stormconduit"):
            raw += 10.0
        return max(0.0, min(raw / 100.0, 0.6))

    # ---------- keystones / item effects ----------
    def active_keystones(self):
        ks = set(self.keystones)
        for it in self.equipped.values():
            g = getattr(it, "grants_keystone", None) if it is not None else None
            if g:
                ks.add(g)
        return ks

    def has_keystone(self, k) -> bool:
        return k in self.active_keystones()

    def item_conversion_rules(self):
        rules = []
        for it in self.equipped.values():
            if it is None:
                continue
            for eff in getattr(it, "effects", []):
                if eff.get("type") == "conversion":
                    rules.append(ConversionRule(eff["from_type"], eff["to_type"],
                                                float(eff["percent"])))
        return rules

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

        # Calculate default speed & update dash state
        speed = self.effective_stats.speed
        if self.slow_timer > 0:
            speed *= self.slow_factor
        if self.stagger_timer > 0:
            speed = 0.0

        # Dash recharge
        if self._dash_charges < self._dash_max_charges:
            self._dash_recharge_timer += dt
            if self._dash_recharge_timer >= self._dash_recharge_rate:
                self._dash_recharge_timer -= self._dash_recharge_rate
                self._dash_charges += 1
        if self._dash_glance_timer > 0:
            self._dash_glance_timer -= dt

        if self._dash_timer > 0:
            self._dash_timer -= dt
            vec = self._dash_vec
            speed = 720.0

        # Move entity using effective vector and speed
        if vec.length_squared() > 0:
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

        if self.combo_timer > 0:
            self.combo_timer -= dt
            if self.combo_timer <= 0:
                self.combo_count = 0

    def face_toward(self, world_x: float, world_y: float):
        """Called by right-click-to-attack: faces the player toward the
        cursor position before the attack hitbox is computed."""
        vec = pygame.Vector2(world_x - self.x, world_y - self.y)
        if vec.length_squared() > 0:
            self.facing = vec.normalize()

    def try_dash(self):
        if (self._dash_charges <= 0 or self._dash_timer > 0
                or self.facing.length_squared() == 0 or self.stagger_timer > 0):
            return False
        self._dash_charges -= 1
        self._dash_vec = pygame.Vector2(self.facing).normalize()
        self._dash_timer = 0.16
        self._dash_glance_timer = 0.35
        self._invuln_timer = max(self._invuln_timer, 0.22)
        return True

    def _rect_at(self, x, y) -> pygame.Rect:
        return pygame.Rect(int(x - self.width / 2), int(y - self.height / 2),
                            self.width, self.height)

    # ---------- combat ----------
    def can_attack(self) -> bool:
        if self.stagger_timer > 0:
            return False
        return self._attack_cooldown_timer <= 0
    

    def start_attack_cooldown(self, profile=None):
        mult = profile.attack_cooldown_multiplier if profile is not None else 1.0
        #self._attack_cooldown_timer = config.PLAYER_ATTACK_COOLDOWN * mult
        spd = getattr(self.effective_stats, "attack_speed_bonus", 0.0)
        denom = max(0.01, 1.0 + spd)                              # ← no zero-div
        self._attack_cooldown_timer = config.PLAYER_ATTACK_COOLDOWN * mult / denom

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

        if not self.alive:
             return False

        amount = int(max(0, amount))                              # ← reject negative
        if amount <= 0:
            return False
        
        if self._invuln_timer > 0:
            if self._dash_timer > 0 or self._dash_glance_timer > 0:
                self.event_bus.emit("near_miss")
            return False
        self.effective_stats.hp -= amount
        self.effective_stats.clamp_hp()
        
        from core.difficulty import D
        self._invuln_timer = D.get("invuln_on_hit")
        #self._invuln_timer = config.PLAYER_INVULN_ON_HIT
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
            #self.base_stats.max_hp += 12
            #self.base_stats.base_damage += 2
            self.base_stats.max_hp += 10
            self.base_stats.base_damage += 1
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

        #from items.equipment_slots import ITEM_SLOT_TO_EQUIP_SLOTS
        allowed = ITEM_SLOT_TO_EQUIP_SLOTS.get(item.slot, [])
        if target_slot not in allowed:
            return False                                # ← B2: reject wrong slot

        old = self.equipped.get(target_slot)
        item_in_inventory = item.item_id in self.inventory.items_by_id

        # Free one inventory slot first (if the new item came from inventory),
        # so the swapped item always has somewhere to go.
        if item_in_inventory:
            self.inventory.remove_item(item)

        if old is not None:
            if not self.inventory.add_item(old):
                # Old item has nowhere to go → abort and roll back.
                if item_in_inventory:
                    self.inventory.add_item(item)
                return False                            # ← B1: don't lose old item

        self.equipped[target_slot] = item
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
            "current_hp": self.effective_stats.hp,                # ← add
            "progression": self.progression.to_dict(),
            "inventory": self.inventory.to_dict(),
            "extension_stones": self.extension_stones,
            "trophies": self.trophies,
            "equipped": {
                slot: (item.to_dict() if item else None)
                for slot, item in self.equipped.items()
            },
            "perks": dict(self.perks),
            "keystones": list(self.keystones),
            "weapon_mastery": dict(self.weapon_mastery)
        }


        # ---------- status effects ----------
    def apply_burn(self, dtype, hit_damage):
        dps = max(1.0, hit_damage * 0.25)
        for b in self._burns:
            if b["dtype"] == dtype:
                b["dps"] = max(b["dps"], dps)
                b["left"] = max(b["left"], 3.0)
                return
        self._burns.append({"dtype": dtype, "dps": dps, "left": 3.0, "acc": 0.0})

    #def apply_slow(self, duration=2.0, factor=0.6):
    #    self.slow_timer = max(self.slow_timer, duration)
    #    self.slow_factor = min(self.slow_factor, factor) if self.slow_timer > 0 else factor

    def apply_slow(self, duration=2.0, factor=0.6):
        if self.slow_timer > 0:
            self.slow_factor = min(self.slow_factor, factor)   # stronger wins
        else:
            self.slow_factor = factor                          # fresh
        self.slow_timer = max(self.slow_timer, duration)

    def apply_stagger(self, duration=0.35):
        if self._stagger_icd > 0:
            return
        self.stagger_timer = max(self.stagger_timer, duration)
        self._stagger_icd = 0.9          # internal cooldown so per-frame hits can't lock us

    def take_dot_damage(self, amount, cause="physical") -> bool:
        if not self.alive:
            return False
        self.effective_stats.hp -= amount
        self.effective_stats.clamp_hp()
        if self.effective_stats.hp <= 0:
            self.alive = False
            self.event_bus.emit("player_death", cause=cause)
        return True

    def tick_status(self, dt):
        if self.slow_timer > 0:
            self.slow_timer -= dt
            if self.slow_timer <= 0:
                self.slow_factor = 1.0
        if self.stagger_timer > 0:
            self.stagger_timer -= dt
        if self._stagger_icd > 0:
            self._stagger_icd -= dt
        if self.guaranteed_crit_timer > 0:
            self.guaranteed_crit_timer -= dt
        for b in list(self._burns):
            b["left"] -= dt
            b["acc"] += dt
            if b["left"] <= 0:
                self._burns.remove(b); continue
            if b["acc"] >= 0.5:
                b["acc"] -= 0.5
                # NOTE: no player_hit emit -> no feedback loop
                self.take_dot_damage(max(1, int(b["dps"] * 0.5)), cause=b["dtype"])

    def reset_status(self):
        self._burns.clear()
        self.slow_timer = 0.0
        self.slow_factor = 1.0    
        self.stagger_timer = 0.0
        self._stagger_icd = 0.0
        self.guaranteed_crit_timer = 0.0

    def heal_to_full(self):
        self.effective_stats.hp = self.effective_stats.max_hp
        self.reset_status()

    @staticmethod
    def from_dict(d, event_bus,codex_manager=None):
        from items.item import Item
        from items.inventory import Inventory as InventoryCls

        # Top-level guard: a corrupted/legacy save may have stored the
        # player payload as a list (or something else). Start fresh
        # rather than crashing the entire load.
        if not isinstance(d, dict):
            d = {}

        p = Player(d.get("x", 0), d.get("y", 0), event_bus, codex_manager=codex_manager)

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
                    #p.equipped[slot] = Item.from_dict(item_dict)
                    #if slot in ITEM_SLOT_TO_EQUIP_SLOTS.get(item.slot, []):   # ← guard
                    #    p.equipped[slot] = item
                    loaded = Item.from_dict(item_dict)
                    #p.equipped[slot] = loaded
                    if slot in ITEM_SLOT_TO_EQUIP_SLOTS.get(loaded.slot, []):
                        p.equipped[slot] = loaded
        elif isinstance(equipped_d, list):
            for entry in equipped_d:
                if not isinstance(entry, dict):
                    continue
                slot = entry.get("slot")
                item_dict = entry.get("item")
                if slot in p.equipped and isinstance(item_dict, dict):
                    loaded = Item.from_dict(item_dict)
                    if slot in ITEM_SLOT_TO_EQUIP_SLOTS.get(loaded.slot, []):
                        p.equipped[slot] = loaded

        p.extension_stones = int(d.get("extension_stones", 0))

        p.trophies = list(d.get("trophies", [])) if isinstance(d.get("trophies", []), list) else []

        perks_d = d.get("perks", {})
        p.perks = {str(k): int(v) for k, v in perks_d.items()} if isinstance(perks_d, dict) else {}
        ks_d = d.get("keystones", [])

        wm_d = d.get("weapon_mastery", {})
        p.weapon_mastery = {str(k): int(v) for k, v in wm_d.items()} \
            if isinstance(wm_d, dict) else {}


        p.keystones = [k for k in ks_d if k in KEYSTONES] if isinstance(ks_d, list) else []
        
        p.recalc_stats()
        hp = d.get("current_hp", p.effective_stats.max_hp)
        if not isinstance(hp, (int, float)):
            hp = p.effective_stats.max_hp
        p.effective_stats.hp = hp

        #p = Player(d.get("x", 0), d.get("y", 0), event_bus,
        #       codex_manager=codex_manager)

        p.effective_stats.clamp_hp()
        p.alive = p.effective_stats.hp > 0



        return p