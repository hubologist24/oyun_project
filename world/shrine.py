"""
Shrine system: interactable world objects (altars, chests, rest points,
and future variants like buff shrines or gamblers).

Design contract:
- Each shrine is a runtime instance placed at (x, y) in a specific area.
- Behavior is defined by a ShrineDef + a Shrine subclass; new shrine types
  are added by (a) registering a ShrineDef in SHRINE_TYPES and (b)
  registering a matching Shrine subclass in SHRINE_CLASSES. Nothing else
  in the codebase changes.

Adding a future shrine (e.g. "gambler", "buff_shrine"):
    SHRINE_TYPES["buff_shrine"] = ShrineDef(
        id="buff_shrine", display_name="Shrine of Fury",
        description="Grants a temporary damage buff.",
        cost_stones=1, cost_items=0, color=(255, 120, 60),
        one_use=False,
    )
    SHRINE_CLASSES["buff_shrine"] = BuffShrine
That's it. All spawning/interaction/drawing uses the registry generically.
"""
import math
import pygame

from dataclasses import dataclass
from typing import Optional

from items.item import Item
from items.item_generator import generate_random_item
from items.affix import RolledAffix
from items.tiers import resolve_tier_for_roll, roll_value_for_tier, get_tier_table
from combat.damage_types import DamageTypes
from world.loot_rules import LootRules


# --------------------------------------------------------------------------
# Base types
# --------------------------------------------------------------------------

@dataclass
class ShrineDef:
    id: str
    display_name: str
    description: str
    cost_stones: int = 0
    cost_items: int = 0
    color: tuple = (200, 200, 255)
    one_use: bool = True


class Shrine:
    INTERACT_RADIUS = 70.0

    def __init__(self, x: float, y: float, definition: ShrineDef,
                 area_id: str = "starting_area"):
        self.x = x
        self.y = y
        self.def_ = definition
        self.area_id = area_id
        self.used = False
        self.anim = 0.0

    def unique_id(self) -> str:
        return f"{self.area_id}:{int(self.x)}:{int(self.y)}:{self.def_.id}"

    @property
    def usable(self) -> bool:
        return not self.used or not self.def_.one_use

    def player_in_range(self, player) -> bool:
        return math.hypot(player.x - self.x, player.y - self.y) <= self.INTERACT_RADIUS

    def can_afford(self, player) -> tuple:
        if self.used and self.def_.one_use:
            return False, "This shrine has been exhausted."
        if player.extension_stones < self.def_.cost_stones:
            return False, f"Requires {self.def_.cost_stones} Extension Stones."
        if (self.def_.cost_items > 0
                and len(player.inventory.items) < self.def_.cost_items):
            return False, f"Requires {self.def_.cost_items} items in inventory."
        return True, ""

    def interaction_hint(self) -> str:
        parts = [f"[E] {self.def_.display_name}"]
        costs = []
        if self.def_.cost_stones:
            costs.append(f"{self.def_.cost_stones} stone")
        if self.def_.cost_items:
            costs.append(f"{self.def_.cost_items} items")
        if costs:
            parts.append("(" + " + ".join(costs) + ")")
        return " ".join(parts)

    def update(self, dt: float):
        self.anim += dt

    # ---- subclasses override ----
    def interact(self, game) -> str:
        raise NotImplementedError

    def draw(self, surface, camera):
        raise NotImplementedError


# --------------------------------------------------------------------------
# Sacrifice Altar
# --------------------------------------------------------------------------

class SacrificeAltar(Shrine):
    def draw(self, surface, camera):
        sx, sy = camera.world_to_screen((self.x, self.y))
        col = (255, 90, 90) if not self.used else (90, 70, 70)
        pygame.draw.circle(surface, (40, 20, 20), (sx, sy + 8), 22)
        pygame.draw.circle(surface, col, (sx, sy), 20, 2)
        for i in range(4):
            a = self.anim * 1.5 + i * math.pi / 2
            rx = sx + int(math.cos(a) * 26)
            ry = sy + int(math.sin(a) * 26)
            pygame.draw.circle(surface, col, (rx, ry), 3)
        if not self.used:
            pygame.draw.circle(surface, (200, 60, 60), (sx, sy), 8)
            pygame.draw.circle(surface, (30, 0, 0), (sx - 3, sy - 2), 2)
            pygame.draw.circle(surface, (30, 0, 0), (sx + 3, sy - 2), 2)

    def interact(self, game) -> str:
        ok, reason = self.can_afford(game.player)
        if not ok:
            return reason

        player = game.player
        items = list(player.inventory.items)[:self.def_.cost_items]
        rng = game.world.rng_service.get_stream("shrine")
        new_item = _forge_sacrifice_reward(items, player, rng,
                                           game._active_world_rules())

        for it in items:
            player.inventory.remove_item(it)
        player.extension_stones -= self.def_.cost_stones

        if player.inventory.add_item(new_item):
            pass
        else:
            game.current_area.loot_drops.append(
                {"x": self.x + 30, "y": self.y, "item": new_item})

        self.used = True
        game.world.used_shrines.add(self.unique_id())
        game.event_bus.emit("item_found", item_id=new_item.item_id,
                            rarity=new_item.rarity,
                            is_six_mod=new_item.is_six_mod())
        return (f"The altar accepts your offering. It returns: "
                f"{new_item.display_name} [{new_item.affix_count_label()}]")


def _forge_sacrifice_reward(sacrificed, player, rng, world_rules) -> Item:
    """
    Reward algorithm — WHAT you sacrifice shapes WHAT you get back:

      1. Damage-type exclusion:
         Collect every damage type present in the offerings. The reward
         is guaranteed to contain a damage type NOT in that set
         (weighted 2.5x toward the player's dominant type so it still
         feels build-relevant when that type wasn't sacrificed).

      2. Item level scaling:
         target_ilvl = avg(sacrificed_ilvl) + rand(1..3)

      3. Rarity / affix density scaling:
         - mostly-rare offerings -> base 2P/2S, ~35% chance of 3P/3S
         - mixed offerings      -> 2P/2S or 1P/2S
         - mostly-common        -> 1P/1S or 1P/2S
         - if avg affixes/offering >= 4: +1 extra P and S with 40% chance
         (capped by the item generator's own limits)

      4. Safety net: if the generated item lacks the chosen damage type,
         one null-type affix is stamped with it (rarely needed because
         favored_damage_types already biases rolls).
    """
    # --- What was offered ---
    sacrificed_types = set()
    total_ilvl = 0
    rarity_score = 0.0
    affix_total = 0
    for it in sacrificed:
        total_ilvl += it.item_level
        affix_total += len(it.prefixes) + len(it.suffixes)
        if it.rarity == Item.RARITY_RARE:
            rarity_score += 3
        elif it.rarity == Item.RARITY_MAGIC:
            rarity_score += 2
        else:
            rarity_score += 1
        for affix in it.all_affixes():
            if affix.damage_type:
                sacrificed_types.add(affix.damage_type)

    avg_ilvl = total_ilvl // max(1, len(sacrificed))
    target_ilvl = avg_ilvl + rng.randint(1, 3)

    # --- Choose the excluded-types damage type ---
    all_types = list(DamageTypes.all())
    available = [t for t in all_types if t not in sacrificed_types] or all_types
    player_main = player.effective_stats.primary_damage_type
    weights = [2.5 if t == player_main else 1.0 for t in available]
    chosen_type = rng.choices(available, weights=weights, k=1)[0]

    # --- Scale affix counts ---
    avg_rarity = rarity_score / max(1, len(sacrificed))
    avg_affixes = affix_total / max(1, len(sacrificed))

    if avg_rarity >= 2.5:
        p = s = 2
        if rng.random() < 0.35:
            p = s = 3
    elif avg_rarity >= 1.8:
        p = s = 2 if rng.random() < 0.5 else 1
    else:
        p = s = 1 if rng.random() < 0.5 else 2

    if avg_affixes >= 4 and rng.random() < 0.40:
        p = min(3, p + 1)
        s = min(3, s + 1)

    # --- Bias affix rolls toward the new type ---
    rules = LootRules(favored_damage_types={chosen_type: 4.0})
    item = generate_random_item(
        rng, area_level=target_ilvl,
        world_context={"world_rules": world_rules, "loot_rules": rules},
        force_affix_counts=(p, s),
    )

    _stamp_damage_type(item, chosen_type, target_ilvl, rng, world_rules)

    item.creation_context = {
        "world_id": "world_0",
        "extension_id": "sacrifice_altar",
        "item_level": target_ilvl,
        "area_level": target_ilvl,
    }
    item.display_name_override = f"Offering-Forged {item.base_name}"
    return item


def _stamp_damage_type(item, dtype, ilvl, rng, world_rules):
    """If `item` has no affix of `dtype`, replace a null-type affix."""
    if any(a.damage_type == dtype for a in item.all_affixes()):
        return
    modifier = f"{dtype}_damage"
    table = get_tier_table(world_rules)
    if modifier not in table:
        return
    try:
        tier = resolve_tier_for_roll(modifier, ilvl, rng, world_rules)
        value = roll_value_for_tier(modifier, tier, rng, world_rules)
    except Exception:
        return
    for lst in (item.prefixes, item.suffixes):
        for i, affix in enumerate(lst):
            if affix.damage_type is None:
                lst[i] = RolledAffix(
                    kind=affix.kind, affix_id=f"shrine_{dtype}",
                    display_name=dtype.title(),
                    modifier=modifier, damage_type=dtype,
                    tier=tier, value=round(value, 1),
                    creation_tier_range=list(table[modifier][tier]),
                )
                item.recompute_flat_bonuses()
                return


# --------------------------------------------------------------------------
# Loot Chest
# --------------------------------------------------------------------------

class LootChest(Shrine):
    def draw(self, surface, camera):
        sx, sy = camera.world_to_screen((self.x, self.y))
        if self.used:
            col, lid = (70, 55, 40), (50, 40, 30)
        else:
            col, lid = (160, 120, 60), (220, 180, 80)
        pygame.draw.rect(surface, col, (sx - 14, sy - 8, 28, 18), border_radius=3)
        pygame.draw.rect(surface, lid, (sx - 14, sy - 12, 28, 8), border_radius=2)
        pygame.draw.rect(surface, (40, 30, 20), (sx - 3, sy - 4, 6, 10))
        if not self.used:
            for i in range(2):
                a = self.anim * 2 + i * math.pi
                px = sx + int(math.cos(a) * 16)
                py = sy - 15 + int(math.sin(a) * 4)
                pygame.draw.circle(surface, (255, 240, 180), (px, py), 2)

    def interact(self, game) -> str:
        if self.used:
            return "The chest is empty."
        player = game.player
        rng = game.world.rng_service.get_stream("loot")
        rules = game._active_world_rules()
        got = []
        for i in range(rng.randint(1, 3)):
            from core.difficulty import D
            quality = D.get("loot_quality_mult")
            loot_rules = LootRules(rare_chance_multiplier=quality,
                                   six_mod_chance_multiplier=quality)
            
            loot = generate_random_item(
                rng, area_level=player.progression.level,
                world_context={
                    "world_rules": rules,
                    "loot_rules": loot_rules,
                    "extension_id": self.area_id
                },
            )
            if player.inventory.add_item(loot):
                got.append(loot.display_name)
            else:
                game.current_area.loot_drops.append(
                    {"x": self.x + 30 + i * 15, "y": self.y + 20, "item": loot})
                got.append(f"{loot.display_name} (dropped)")
        self.used = True
        game.world.used_shrines.add(self.unique_id())
        return "Chest opened: " + (", ".join(got) if got else "empty")


# --------------------------------------------------------------------------
# Rest Shrine (starting safe zone, reusable)
# --------------------------------------------------------------------------

class RestShrine(Shrine):
    def draw(self, surface, camera):
        sx, sy = camera.world_to_screen((self.x, self.y))
        pulse = 1.0 + 0.2 * math.sin(self.anim * 3)
        pygame.draw.circle(surface, (80, 180, 255), (sx, sy), int(28 * pulse), 2)
        pygame.draw.circle(surface, (150, 220, 255), (sx, sy), 10)
        pygame.draw.circle(surface, (255, 255, 255), (sx, sy), 6)

    def interact(self, game) -> str:
        player = game.player
        if player.effective_stats.hp >= player.effective_stats.max_hp:
            return "You are already at full health."
        player.heal_to_full()
        game.terrain.clear_hostile()
        return "You rest at the shrine. Fully healed."


# --------------------------------------------------------------------------
# Stash Chest (opens stash UI)
# --------------------------------------------------------------------------

class StashChest(Shrine):
    def draw(self, surface, camera):
        sx, sy = camera.world_to_screen((self.x, self.y))
        pygame.draw.rect(surface, (100, 70, 40), (sx - 16, sy - 12, 32, 22), border_radius=3)
        pygame.draw.rect(surface, (150, 120, 70), (sx - 16, sy - 16, 32, 8), border_radius=2)
        pygame.draw.rect(surface, (220, 180, 60), (sx - 3, sy - 6, 6, 12))
        pygame.draw.circle(surface, (240, 220, 130), (sx, sy - 12), 3)

    def interact(self, game) -> str:
        if not game.inventory_ui.visible:
            game.inventory_ui.toggle()
        if not game.inventory_ui.stash_visible:
            game.inventory_ui.toggle_stash()
        return ""


# --------------------------------------------------------------------------
# Registry — extend here for new shrine types
# --------------------------------------------------------------------------

SHRINE_TYPES = {
    "sacrifice_altar": ShrineDef(
        id="sacrifice_altar", display_name="Altar of Sacrifice",
        description="Offer 1 stone + 5 items. Receive an item whose damage "
                    "type you did NOT offer. Better offerings -> better reward.",
        cost_stones=1, cost_items=5, color=(220, 70, 70)),
    "loot_chest": ShrineDef(
        id="loot_chest", display_name="Treasure Chest",
        description="A forgotten cache.",
        color=(200, 160, 80)),
    "rest_shrine": ShrineDef(
        id="rest_shrine", display_name="Shrine of Rest",
        description="Fully restores health.",
        color=(120, 200, 255), one_use=False),
    "stash_chest": ShrineDef(
        id="stash_chest", display_name="Stash Chest",
        description="Opens your Stash.",
        color=(160, 130, 70), one_use=False),
}

SHRINE_CLASSES = {
    "sacrifice_altar": SacrificeAltar,
    "loot_chest": LootChest,
    "rest_shrine": RestShrine,
    "stash_chest": StashChest,
}


def make_shrine(shrine_type: str, x: float, y: float, area_id: str) -> Shrine:
    return SHRINE_CLASSES[shrine_type](x, y, SHRINE_TYPES[shrine_type], area_id)