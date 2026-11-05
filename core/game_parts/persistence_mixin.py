"""Save / load. The duplicate Player.from_dict call has been removed."""
from core.difficulty import D
from player.player import Player
from world.level import build_level
from world.world import World
from world.camera import Camera
from world.expansion import attach_extension
from history.event_log import EventLog
from history.exploration import ExplorationTracker
from core.game_parts.constants import _NullEventBus


class PersistenceMixin:

    def _save_game(self):
        state = {
            "world": self.world.to_dict(),
            "player": self.player.to_dict(),
            "current_extension_id": self.current_extension_id,
            "event_log": self.event_log.to_dict(),
            "exploration": self.exploration.to_dict(),
            "nemesis": self.nemesis.to_dict(),
            "used_shrines": sorted(self.world.used_shrines),
            "feedback": self.feedback,
        }
        state["player"]["current_hp"] = self.player.effective_stats.hp
        self.save_manager.save(state)
        self.save_manager.save_stash(self.player.stash)
        self._show_message("Game saved.")

    def _load_game(self):
        data = self.save_manager.load()
        if not data:
            self._show_message("No save file found.")
            return

        # --- 1. Reset in-flight / transient state ---
        self._echoes.clear()
        fb = data.get("feedback")
        self.feedback = fb if isinstance(fb, dict) else {"too_hard": 0, "too_easy": 0, "avoid_biomes": []}

        self._clear_player_projectiles()
        self._hitstop_timer = 0.0
        self._respawn_timer = 1.5
        self._pending_extension_spec = None
        self._pending_gate = None
        self._pending_evolution_mode = None
        self._pending_rerolls_left = 0
        self._boss_attempt_active = False
        self._boss_attempt_boss_id = None

        self._deaths_since_boss_kill = 0
        self._boss_deaths_this_attempt = 0

        # Close any modal UI that happened to be open at save time.
        if self.evolution_banner.visible:
            self.evolution_banner.dismiss()
        if self.profile_screen.visible:
            self.profile_screen.hide()
        if self.gateway_menu.visible:
            self.gateway_menu.hide()
        if self.inventory_ui.visible:
            self.inventory_ui.toggle()

        # --- 2. Restore world & player ---
        self.world = World.from_dict(data["world"])

        self.player = Player.from_dict(data["player"], self.event_bus,
                                       codex_manager=self.codex)
        self.player.stash = self.save_manager.load_stash()
        self.player.clear_move_target()

        #self.camera = Camera(*self.starting_area.tilemap.pixel_size())

        self.reactions.reset()
        self.dialogue = None
        self.nemesis.load(data.get("nemesis"))

        # --- 3. Restore event log WITHOUT re-subscribing ---
        # Rebuild into a throwaway bus and transplant just the events list
        # onto the one long-lived EventLog instance.
        if "event_log" in data:
            tmp = EventLog.from_dict(data["event_log"], _NullEventBus())
            self.event_log.events = tmp.events
        else:
            self.event_log.events = []

        if "exploration" in data:
            self.exploration = ExplorationTracker.from_dict(data["exploration"])
        else:
            self.exploration = ExplorationTracker()

        self.kinetic.reset()
        self.kinetic.restore_from_log(self.event_log)

        # --- 4. Rebuild the single map and replay expansions in order ---
        self.starting_area = build_level(self.world.rng_service)
        self.current_area = self.starting_area
        self.current_area_id = "starting_area"
        self.current_extension_id = None

        # Keep the starting area pacified if its boss was already killed.
        if "starting_area" in self.world.cleared_regions:
            self.starting_area.bosses = []

        self._apply_shrine_persistence()

        self.exploration.register_area("starting_area", self.starting_area.tilemap)
        self._ensure_starting_gate_registered()

        self.camera = Camera(*self.current_area.tilemap.pixel_size())

        self._apply_shrine_persistence()

        for ext in self.world.extensions:
            gate = next((g for g in self.world.gates
                         if g.target_extension_id == ext.extension_id), None)
            if gate is None:
                continue
            attach_extension(self.current_area, ext, gate,
                             self.world.rng_service, self.world)
            self._on_map_expanded(ext.extension_id)

        self._restamp_gates()

        self._apply_shrine_persistence()

        self.packs.reset()
        self.terrain.reset()
        self.packs.assign_new()

        #self.camera = Camera(*self.current_area.tilemap.pixel_size())
        self._update_current_region()
        self.camera.update(self.player.x, self.player.y)
        D.set_player_level(self.player.progression.level)
        self._ambient_event_timer = self._roll_ambient_event_timer()
        self._show_message("Game loaded.")
