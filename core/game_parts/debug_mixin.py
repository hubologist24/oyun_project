"""Debug helpers (F2/F3/F5/F7/F12)."""
import core.config as config
from items.item_generator import generate_random_item


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
