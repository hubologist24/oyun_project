"""
Main game loop and state orchestration - Phase 8 (split into mixins).
Behaviour lives in core/game_parts/*; this file owns construction + the loop.
"""
import sys
import random
import pygame

import core.config as config
from core.events import EventBus
from core.difficulty import D
from core import audio
from core.nemesis_manager import NemesisManager

from player.player import Player
from world.level import build_level
from world.world import World
from world.camera import Camera
from save.save_manager import SaveManager
from ui.hud import HUD, InventoryUI, DamageNumbers
from ui.world_evolution import WorldEvolutionBanner
from ui.profile_screen import ProfileScreen
from ui.gateway_menu import GatewayMenu
from ui.choice_menu import ChoiceMenu
from history.event_log import EventLog
from history.exploration import ExplorationTracker
from history.codex import CodexManager
from combat.reactions import ReactionSystem

from core.game_parts.input_mixin import InputMixin
from core.game_parts.draw_mixin import DrawMixin
from core.game_parts.combat_mixin import CombatMixin
from core.game_parts.loot_mixin import LootMixin
from core.game_parts.extension_mixin import ExtensionMixin
from core.game_parts.progression_mixin import ProgressionMixin
from core.game_parts.pressure_mixin import PressureMixin
from core.game_parts.ambient_mixin import AmbientMixin
from core.game_parts.persistence_mixin import PersistenceMixin
from core.game_parts.debug_mixin import DebugMixin

import math
from world import swing_fx


class Game(InputMixin, DrawMixin, CombatMixin, LootMixin, ExtensionMixin,
           ProgressionMixin, PressureMixin, AmbientMixin,
           PersistenceMixin, DebugMixin):

    def __init__(self):
        pygame.init()
        pygame.display.set_caption("AI-Evolving ARPG - Phase 8 Prototype (Gates & Gateway Portal)")
        self.screen = pygame.display.set_mode((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        self.clock = pygame.time.Clock()
        self.running = True
        self.player_projectiles = []
        self.player_swings = []
        self._fx_surface = pygame.Surface(
            (config.SCREEN_WIDTH, config.SCREEN_HEIGHT), pygame.SRCALPHA)

        self.event_bus = EventBus()
        self.save_manager = SaveManager()
        self.event_log = EventLog(self.event_bus)
        self.exploration = ExplorationTracker()
        self.extension_generator = self._build_extension_generator()

        self.world = World(random.randint(0, 2**31 - 1))
        audio.init()
        self._hitstop_timer = 0.0
        self._respawn_timer = 1.5

        self.starting_area = build_level(self.world.rng_service)
        self.current_area = self.starting_area
        self.current_extension_id = None
        self.current_area_id = "starting_area"
        self.exploration.register_area(self.current_area_id, self.current_area.tilemap)
        self._ensure_starting_gate_registered()

        self.player = Player(*self.starting_area.start_pos, self.event_bus)
        self.player.stash = self.save_manager.load_stash()
        self.camera = Camera(*self.starting_area.tilemap.pixel_size())

        self.hud = HUD()
        self.inventory_ui = InventoryUI()
        self.evolution_banner = WorldEvolutionBanner()
        self.profile_screen = ProfileScreen()
        self.gateway_menu = GatewayMenu()
        self.choice_menu = ChoiceMenu()

        self._echoes = []
        self.feedback = {"too_hard": 0, "too_easy": 0, "avoid_biomes": []}
        self.message = ""
        self._message_timer = 0.0
        self.crit_bursts = []

        self._pending_extension_spec = None
        self._pending_gate = None
        self._pending_evolution_mode = None
        self._pending_rerolls_left = 0

        self._move_button_held = False
        self._attack_button_held = False
        self._boss_attempt_active = False
        self._boss_attempt_boss_id = None

        self.show_region_details = False
        self._roam_timer = 0.0
        self._safe_room_heal_acc = 0.0
        self._info_font = pygame.font.SysFont("consolas", 14)
        self._info_font_b = pygame.font.SysFont("consolas", 16, bold=True)
        self.damage_numbers = DamageNumbers()
        self.dialogue = None

        self.reactions = ReactionSystem(self)
        from combat.kinetic import KineticTracker
        self.kinetic = KineticTracker(self)

        from enemies.packs import PackManager
        from world.terrain import TerrainSystem
        self.terrain = TerrainSystem(self)
        self.packs = PackManager(self)
        self.packs.assign_new()
        self.nemesis = NemesisManager(self)

        self.codex = CodexManager()
        self.player.codex_manager = self.codex
        self.player.recalc_stats()

        self._boss_deaths_this_attempt = 0
        self._pressure_ambush_timer = 0.0
        self._pressure_hazard_timer = 0.0
        self._deaths_since_boss_kill = 0

        self._register_events()
        self._grant_starting_gear()
        D.set_player_level(self.player.progression.level)
        self._ambient_event_timer = self._roll_ambient_event_timer()

    # ---------------- core loop ----------------
    def _show_message(self, text, duration=2.5):
        self.message = text
        self._message_timer = duration

    def run(self):
        while self.running:
            dt = self.clock.tick(config.FPS) / 1000.0
            self._handle_events()
            self._update(dt)
            self._draw()
        pygame.quit()
        sys.exit()

    # PASTE _update() HERE unchanged (it's the one big method that stays).
    # --- Add these imports at the top of core/game.py ---
# import math
# from world import swing_fx

    def _update(self, dt):
        if self.dialogue:
            self.dialogue["t"] -= dt
            if self.dialogue["t"] <= 0:
                self.dialogue = None

        self.player.tick_status(dt)

        if self._hitstop_timer > 0:
            self._hitstop_timer -= dt
            self.damage_numbers.update(dt)

            for b in self.crit_bursts:
                b["life"] -= dt
            self.crit_bursts = [b for b in self.crit_bursts if b["life"] > 0]

            if self._message_timer > 0:
                self._message_timer -= dt
                if self._message_timer <= 0:
                    self.message = ""
            self.camera.tick(dt)
            self.camera.update(self.player.x, self.player.y)
            return

        self.damage_numbers.update(dt)

        if self._message_timer > 0:
            self._message_timer -= dt
            if self._message_timer <= 0:
                self.message = ""

        self.world.total_playtime_seconds += dt

        if (self.evolution_banner.visible or self.profile_screen.visible
                or self.gateway_menu.visible or self.inventory_ui.visible
                or self.choice_menu.visible):
            return

        # ---- safe room regeneration ----
        if self.player.alive:
            safe = self.starting_area.safe_room_rect
            if safe.collidepoint(self.player.x, self.player.y):
                stats = self.player.effective_stats
                if stats.hp < stats.max_hp:
                    self._safe_room_heal_acc += stats.max_hp * D.get("safe_room_regen") * dt
                    if self._safe_room_heal_acc >= 1.0:
                        heal = int(self._safe_room_heal_acc)
                        self._safe_room_heal_acc -= heal
                        stats.hp = min(stats.max_hp, stats.hp + heal)

        # ---- shrines tick ----
        for shrine in self.current_area.shrines:
            shrine.update(dt)

        # DoT ticks and telegraphed novas must not resolve while a menu is open.
        self.reactions.tick(dt)
        self._tick_echoes(dt)
        self.nemesis.update(dt)

        if self.player.alive:
            keys = pygame.key.get_pressed()
            self.player.handle_input(dt, keys, self.current_area.tilemap)
            self.kinetic.update(dt)
            self._pickup_loot()
            self._update_current_region()
            self._update_region_pressure(dt)
            self._update_roaming_spawns(dt)
            self._tick_ambient_events(dt)
            self.exploration.update(dt, self.current_area_id, self.player.x, self.player.y)
            if self._attack_button_held:
                world_pos = self.camera.screen_to_world(pygame.mouse.get_pos())
                self.player.face_toward(*world_pos)
                self._try_attack()
        else:
            self._respawn_timer -= dt
            if self._respawn_timer <= 0:
                self._respawn_player()

        world_rules = self._active_world_rules()

        # If the tracked boss attempt's boss is dead or the player has
        # wandered far away, close out the attempt.
        if self._boss_attempt_active and self._boss_attempt_boss_id:
            active_boss = next(
                (b for b in self.current_area.bosses
                 if b.name == self._boss_attempt_boss_id and b.alive), None)
            if (active_boss is None
                    or math.hypot(active_boss.x - self.player.x,
                                  active_boss.y - self.player.y) > 1500):
                self.event_bus.emit("boss_attempt_ended", result="fled",
                                    boss_id=self._boss_attempt_boss_id)
                self._boss_attempt_active = False
                self._boss_attempt_boss_id = None

        self.packs.update(dt)
        self.terrain.update(dt)

        px, py = self.player.x, self.player.y
        for enemy in self.current_area.enemies:
            if abs(enemy.x - px) > 1200 or abs(enemy.y - py) > 1200:
                continue
            enemy.update(dt, self.player, self.current_area.tilemap, self.event_bus,
                         world_rules=world_rules)

        if self.player.alive:
            for boss in self.current_area.bosses:
                if boss.alive and abs(boss.x - px) < 1200 and abs(boss.y - py) < 1200:
                    boss.update(dt, self.player, self.event_bus, world_rules=world_rules)
                    if boss.summon_requests:
                        self._spawn_boss_adds(boss)

        self._update_player_projectiles(dt)

        swing_fx.tick(self.player_swings, dt)

        self.camera.update(self.player.x, self.player.y)
        self.camera.tick(dt)
        self._check_automatic_extension()
