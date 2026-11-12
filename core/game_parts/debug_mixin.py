"""Debug helpers (F2/F3/F5/F7/F12)."""
import core.config as config
from items.item_generator import generate_random_item

from core.difficulty import D


class DebugMixin:

    def _debug_spawn_item(self, force_rarity=None, force_affix_counts=None, item_level_override=None):
        stream = self.world.rng_service.get_stream("debug")
        area_level = item_level_override or self.player.progression.level
        item = generate_random_item(
            stream, area_level=area_level,
            force_rarity=force_rarity, force_affix_counts=force_affix_counts,
        )
        self.player.inventory.add_item(item)
        tag = f"[{item.affix_count_label()}]"
        self._show_message(f"Debug: {item.display_name} {tag} ilvl {item.item_level}")
        self.event_bus.emit("item_found", item_id=item.item_id, rarity=item.rarity,
                            is_six_mod=item.is_six_mod())

    def _debug_print_world_info(self):
        rule_set = self._active_world_rules()
        print("=== ACTIVE WORLD RULES ===")
        for line in rule_set.summary_lines():
            print(f"  {line}")
        print("=== TIER OVERRIDES (aggregated) ===")
        for mod, tiers in rule_set.tier_overrides.items():
            print(f"  {mod}: {tiers}")
        print(f"=== EXTENSIONS: {len(self.world.extensions)} ===")
        for ext in self.world.extensions:
            print(f"  {ext.extension_id}: {ext.spec.extension_name} (lvl {ext.spec.area_level}, "
                  f"anomaly={ext.spec.is_anomaly})")
        print(f"=== GATES: {len(self.world.gates)} "
              f"({len(self.world.open_gates())} open / {len(self.world.closed_gates())} closed) ===")
        for gate in self.world.gates:
            target = gate.target_extension_id or "-"
            print(f"  gate#{gate.gate_id} in '{gate.owner_area_id}' -> '{target}' ({gate.state})")
        print(f"=== Seconds since last extension: {self.world.seconds_since_last_extension():.1f} "
              f"(threshold: {config.WORLD_EXTENSION_SECONDS}) ===")
        self._show_message("World rule info printed to console (F7).")



    def _apply_test_level(self, level, force_weapon_subtype=None):
        level = max(1, int(level))

        if force_weapon_subtype:
            from items.weapon_subtypes import WeaponSubtypes
            if not WeaponSubtypes.is_valid(force_weapon_subtype):
                raise ValueError(f"Invalid weapon subtype: {force_weapon_subtype!r}")

        self._level_up_to(level)
        self._grant_full_gear_at_level(level, force_weapon_subtype=force_weapon_subtype)
        D.set_player_level(level)
        self.player.recalc_stats()
        self.player.heal_to_full()
        note = f" (weapon: {force_weapon_subtype})" if force_weapon_subtype else ""
        self._show_message(
            f"TEST SETUP: Level {level} with a full randomized gear set{note}",
            duration=5.0,
        )

    def _grant_full_gear_at_level(self, level, force_weapon_subtype=None):
        from items.item_generator import generate_random_item

        rng = self.world.rng_service.get_stream(f"test_gear_{level}")

        slot_targets = [
            ("weapon",  "weapon"),
            ("head",    "armor"),
            ("chest",   "armor"),
            ("legs",    "armor"),
            ("boots",   "armor"),
            ("gloves",  "armor"),
            ("ring_1",  "ring"),
            ("ring_2",  "ring"),
            ("amulet",  "amulet"),
        ]

        for target_slot, category in slot_targets:
            item = None
            kwargs = {}
            if target_slot == "weapon" and force_weapon_subtype:
                kwargs["force_weapon_subtype"] = force_weapon_subtype

            for _ in range(40):
                candidate = generate_random_item(
                    rng, area_level=level, force_slot=category, **kwargs
                )
                if target_slot.startswith("ring"):
                    if candidate.slot == "ring":
                        item = candidate
                        break
                elif candidate.slot == target_slot:
                    item = candidate
                    break

            if item is None:
                print(f"[Test] Could not roll a {target_slot} item at level {level}")
                continue

            self.player.equipped[target_slot] = item
            subtype_tag = f" (subtype={item.weapon_subtype})" if getattr(item, "weapon_subtype", None) else ""
            print(f"[Test] {target_slot}: {item.display_name}{subtype_tag} "
                  f"[{item.affix_count_label()}] ilvl {item.item_level}")

    def _level_up_to(self, target_level):
        guard = 0
        while self.player.progression.level < target_level and guard < 500:
            remaining = (self.player.progression.xp_to_next()
                         - self.player.progression.xp)
            if remaining <= 0:
                remaining = 1
            self.player.gain_xp(remaining)
            guard += 1

