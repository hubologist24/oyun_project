"""
Main game loop and state orchestration - Phase 8.

Adds:
- WorldExtensionGenerator (Procedural backend + WorldValidator + fallback)
- Automatic extension generation check (WORLD_EXTENSION_HOURS-based)
- F11 = manual generation, Shift+F11 = debug/forced generation
  (bypasses playtime gate but still goes through the same
  generator+validator pipeline -- 'debug' just means 'ignore the timer',
  never 'skip validation')
- Gate system: every area has >=1 closed gate; world evolution opens
  one existing closed gate and links it to the new extension.
- Gateway Portal fast-travel menu (TAB) listing all open-gate destinations.
- Mouse controls: left-click walk, right-click attack toward cursor.
"""
import sys
import random
#from combat import damage
from enemies import boss
import pygame

import core.config as config
from core.rng import RNGService
from core.events import EventBus
from player.player import Player

from world.level import build_level
from world.expansion import attach_extension, find_door_site, stamp_door
from world.world import World
from world.extension_generator import WorldExtensionGenerator
from world.camera import Camera
from combat.combat import player_attack_enemy
from items.item_generator import generate_random_item
from save.save_manager import SaveManager
from ui.hud import HUD, InventoryUI, DamageNumbers
from ui.world_evolution import WorldEvolutionBanner
from ui.profile_screen import ProfileScreen
from ui.gateway_menu import GatewayMenu
from history.event_log import EventLog
from history.exploration import ExplorationTracker
from history.aggregator import build_player_profile

from combat.reactions import ReactionSystem
from core.nemesis_manager import NemesisManager
from enemies.dialogue import (boss_engage_line, boss_phase_line,
                              boss_death_taunt, boss_last_words)

from enemies.enemy import spawn_basic_enemy
from world import swing_fx

import math
import textwrap

from core import audio

from history.codex import CodexManager



import time

MAX_ON_KILL_DEPTH = 3
CRIT_BURST_RADIUS = 70.0
CRIT_BURST_RATIO = 0.50


BOSS_ITEM_DROPS = [
    (0.90, (3, 2)),   # near-guaranteed best roll
    (0.75, (2, 2)),
    (0.60, (2, 1)),
    (0.40, (1, 2)),   # bonus slot
]
BOSS_STONE_DROPS = [0.70, 0.30, 0.10]   # 1st stone, 2nd stone, 3rd stone

WORLD_BOSS_ITEM_CHANCE_BONUS = 0.15     # added to each slot
WORLD_BOSS_EXTRA_ITEM_SLOT = (0.45, (3, 3))
WORLD_BOSS_STONE_CHANCE_BONUS = 0.10 


PRESSURE_THRESHOLDS = [0, 90, 180, 300, 450]      # seconds → level 0..4
PRESSURE_LABELS = ["Calm", "Uneasy", "Restless", "Hostile", "Corrupted"]
PRESSURE_COLORS = [(150,150,150),(200,200,120),(220,140,80),(255,80,80),(200,40,200)]


class _NullEventBus:
    """
    Absorbs subscriptions (and drops emits) while we rebuild an EventLog
    from a save. We only want the restored .events list -- not a fresh
    set of 15 callbacks attached to the live bus.
    """
    def subscribe(self, *args, **kwargs):
        return None
    def unsubscribe(self, *args, **kwargs):
        return None
    def emit(self, *args, **kwargs):
        return None

class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("AI-Evolving ARPG - Phase 8 Prototype (Gates & Gateway Portal)")
        self.screen = pygame.display.set_mode((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        self.clock = pygame.time.Clock()
        self.running = True
        self.player_projectiles = []

        self.player_swings = []
        self._fx_surface = pygame.Surface(
            (config.SCREEN_WIDTH, config.SCREEN_HEIGHT), pygame.SRCALPHA
)

        self.event_bus = EventBus()
        self.save_manager = SaveManager()

        self.event_log = EventLog(self.event_bus)
        self.exploration = ExplorationTracker()
        self.extension_generator = self._build_extension_generator()

        world_seed = random.randint(0, 2**31 - 1)
        self.world = World(world_seed)

        audio.init()

        self._hitstop_timer = 0.0

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

        self.message = ""
        self._message_timer = 0.0

        self.crit_bursts = []

        self._pending_extension_spec = None
        self._pending_gate = None
        self._pending_evolution_mode = None
        self._pending_rerolls_left = 0

        self._move_button_held = False          # NEW
        self._attack_button_held = False        # NEW

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

        # Codex: attach to the ALREADY-BUILT player (do NOT reconstruct)
        self.codex = CodexManager()
        self.player.codex_manager = self.codex
        self.player.recalc_stats()          # reapply so legacy bonuses take effect

        # NEW: pressure/attempt tracking
        self._boss_deaths_this_attempt = 0
        self._pressure_ambush_timer = 0.0
        self._pressure_hazard_timer = 0.0

        self._register_events()



    def _on_critical_hit(self, x, y, damage_type, crit_damage, **kwargs):
        """Elemental crit detonation: on a crit, splash a small AoE of the
        same damage type around the struck enemy. Enables a crit-focused
        build to clear packs, not just single targets."""
        from combat.combat import _enemy_category
        from combat.damage import resolve_damage

        rules = self._active_world_rules()
        explosion_damage = max(1, int(crit_damage * CRIT_BURST_RATIO))
        radius = CRIT_BURST_RADIUS

        # Splash to nearby enemies (not the original target -- it already ate
        # the crit, and double-dipping would make crits absurd).
        for other in list(self.current_area.enemies):
            if not other.alive:
                continue
            d = math.hypot(other.x - x, other.y - y)
            if d < 1.0 or d > radius:
                continue
            dmg = resolve_damage(explosion_damage, damage_type, None,
                                target_resistances=other.resistances,
                                world_rules=rules,
                                target_category=_enemy_category(other),
                                source_category="player")
            other.take_damage(dmg)
            self.damage_numbers.spawn(other.x, other.y - 25, str(dmg),
                                    self._damage_type_color(damage_type))
            if not other.alive:
                self._on_enemy_killed(other)

        # Also splash onto bosses if they're in range (blink duels get spicy).

        for boss in self.current_area.bosses:
            if not boss.alive:
                continue
            if math.hypot(boss.x - x, boss.y - y) > radius:
                continue
            dmg = resolve_damage(explosion_damage, damage_type, None,
                                target_resistances=boss.resistances,
                                world_rules=rules,
                                target_category="boss",
                                source_category="player")
            boss.take_damage(dmg)
            self.damage_numbers.spawn(boss.x, boss.y - 40, str(dmg),
                                    self._damage_type_color(damage_type))

            # Splash-damage that reaches a phase boundary must still fire the
            # phase cutscene (previously the effect was silently skipped).
            if getattr(boss, "phase_just_changed", False):
                boss.phase_just_changed = False
                self._trigger_phase_transition(boss)

            # Splash-kill on a boss must also go through the normal kill path
            # (loot burst, event log, dialogue) instead of just zeroing HP.
            if not boss.alive:
                self._on_boss_killed(boss)


    def _apply_overcharge_to_spec(self, spec, overcharge):
        rng = self.world.rng_service.get_stream("overcharge")
        spec.area_level += overcharge * 3
        spec.is_anomaly = True

        chaos_dtype = rng.choice(["chaos", "lightning"])
        if not spec.loot_rules:
             spec.loot_rules = {}
        spec.loot_rules["rare_chance_multiplier"] = (
             spec.loot_rules.get("rare_chance_multiplier", 1.0) * (1 + 0.3 * overcharge))
        spec.loot_rules["six_mod_chance_multiplier"] = (
             spec.loot_rules.get("six_mod_chance_multiplier", 1.0) * (1 + 0.2 * overcharge))

        overcharge_mod = {
             "name": f"Overcharged Reality ({chaos_dtype.title()})",
             "description": f"The overcharged gate spills unstable {chaos_dtype} "
                             f"energy into this region.",
             "rules": [
                 {"type": "resistance_modifier", "target": "player",
                 "damage_type": chaos_dtype, "delta": -0.20},
                 {"type": "damage_dealt_multiplier", "target": "elite",
                 "damage_type": None, "multiplier": 1.25},
             ],
         }
         # Respect MAX_MODIFIERS = 3: replace the last modifier rather than
         # adding a fourth.
        if len(spec.world_modifiers) < 3:
             spec.world_modifiers.append(overcharge_mod)
        else:
             spec.world_modifiers[-1] = overcharge_mod
        spec.boss_personalization = dict(spec.boss_personalization or {})
        spec.boss_personalization["difficulty_bias"] = "high"            

    def _damage_type_color(self, dtype):
        return {
            "fire":      (255, 130, 40),
            "cold":      (120, 200, 255),
            "lightning": (255, 240, 120),
            "chaos":     (200, 90, 255),
            "physical":  (220, 220, 220),
            "poison":    (140, 230, 120),
            "bleed":     (220, 60, 60),
        }.get(dtype, (255, 255, 255))

    def _update_roaming_spawns(self, dt):
        self._roam_timer -= dt
        if self._roam_timer > 0:
            return
        self._roam_timer = 2.5
        px, py = self.player.x, self.player.y
        near = sum(1 for e in self.current_area.enemies
                if abs(e.x - px) < 1000 and abs(e.y - py) < 1000)
        if near >= 45:
            return
        ts = config.TILE_SIZE
        grid = self.current_area.tilemap.grid
        rng = self.world.rng_service.get_stream("roaming")
        pc, pr = int(px // ts), int(py // ts)
        for _ in range(60):
            c, r = pc + rng.randint(-24, 24), pr + rng.randint(-16, 16)
            if not (0 <= r < len(grid) and 0 <= c < len(grid[0])) or grid[r][c] != 0:
                continue
            x, y = c * ts + ts / 2, r * ts + ts / 2
            if math.hypot(x - px, y - py) < 520:      # off-screen
                continue
            rid = self.current_area.region_at_tile(c, r)
            ext = self.world.get_extension(rid)
            lvl = ext.spec.area_level if ext else max(2, self.player.progression.level)
            e = spawn_basic_enemy(x, y, rng, area_level=lvl)
            e.region_id = rid

            e._pack_checked = True      # roamers never join packs

            self.current_area.enemies.append(e)
            break

    def _build_extension_generator(self):
        """
        Builds the WorldExtensionGenerator with whichever backend is
        selected in core/config.py. Defaults to pure procedural (zero
        external dependency), per the project's core requirement that
        the game must work without an external AI API.
        """
        from world.extension_generator import WorldExtensionGenerator
        from ai.procedural_generator import ProceduralWorldGenerator

        mode = config.AI_BACKEND_MODE
        procedural = ProceduralWorldGenerator()

        if mode == "mock_ai":
            from ai.mock_generator import MockAIWorldGenerator
            backend = MockAIWorldGenerator(failure_rate=config.MOCK_AI_FAILURE_RATE)
            print("[Game] Using MockAIWorldGenerator (offline LLM simulation) as primary backend.")
            return WorldExtensionGenerator(backend=backend, fallback_backend=procedural)

        if mode == "llm":
            from ai.llm_generator import LLMWorldGenerator
            backend = LLMWorldGenerator(provider=config.LLM_PROVIDER)
            if backend.is_available():
                print(f"[Game] Using LLMWorldGenerator (provider='{config.LLM_PROVIDER}', "
                      f"model='{backend.model}') as primary backend.")
            else:
                print(f"[Game] LLMWorldGenerator requested (provider='{config.LLM_PROVIDER}') "
                      f"but not available (missing key or package). Will fall back to procedural.")
            return WorldExtensionGenerator(backend=backend, fallback_backend=procedural)

        print("[Game] Using ProceduralWorldGenerator only.")
        return WorldExtensionGenerator(backend=procedural, fallback_backend=procedural)

    def _ensure_starting_gate_registered(self):
        existing = self.world.gates_in_area("starting_area")
        if existing:
            for g in existing:
                stamp_door(self.starting_area, g)
            return
        site = find_door_site(self.starting_area.tilemap.grid,
                              [self.starting_area.gate_room])
        if site:
            c, r, side, _ = site
            gate = self.world.register_starting_gate(0, c, r, side)
            stamp_door(self.starting_area, gate)

    def _register_events(self):
        self.event_bus.subscribe("player_death", self._on_player_death)
        self.event_bus.subscribe("level_up", lambda level: self._show_message(f"Level Up! Now level {level}"))
        self.event_bus.subscribe("player_hit", self._on_player_hit_feedback)
        self.event_bus.subscribe("level_up", self._on_level_up)
        self.event_bus.subscribe("critical_hit", self._on_critical_hit)
        self.event_bus.subscribe("attacker_info", self.nemesis.on_attacker_info)
        self.event_bus.subscribe("area_entered", self.nemesis.on_area_entered)
        self.event_bus.subscribe("kinetic_technique", self._on_kinetic_technique)


    def _on_kinetic_technique(self, name=None, **kw):
         if not name:
             return
         newly = self.codex.record("kinetic_technique", context={"name": name})
         if newly:
             self._handle_codex_unlocks(newly)
    
    def _on_level_up(self, level):
            from core import audio
            audio.play("level_up")
            #self._show_message(f"Level Up! Now level {level}")

    def _say(self, speaker, text, duration=4.5):
        self.dialogue = {"speaker": speaker, "text": text, "t": duration}

    def _boss_greeting(self, boss):
        attempts = sum(1 for e in self.event_log.events_of_type("boss_attempt_started")
                       if e.payload.get("boss_id") == boss.name)
        fallen = [e.payload.get("name") for e in self.event_log.events_of_type("boss_killed")
                  if e.payload.get("name") != boss.name]
        self._say(boss.name, boss_engage_line(attempts, self._current_profile().main_damage, fallen))

    def _draw_dialogue(self):
        if not self.dialogue:
            return
        d = self.dialogue
        txt = self._info_font_b.render(f'{d["speaker"]}: "{d["text"]}"', True, (255, 210, 230))
        bg = pygame.Surface((txt.get_width() + 24, txt.get_height() + 12), pygame.SRCALPHA)
        bg.fill((10, 10, 16, 200))
        x = config.SCREEN_WIDTH // 2 - bg.get_width() // 2
        y = config.SCREEN_HEIGHT - 130
        self.screen.blit(bg, (x, y))
        self.screen.blit(txt, (x + 12, y + 6))

    

    def _on_player_hit_feedback(self, source=None, damage=0, damage_type=None):
        self.damage_numbers.spawn(self.player.x, self.player.y - 30, str(damage), (255, 90, 90))
        self.camera.shake(magnitude=6, duration=0.20) #shake
        self._trigger_hitstop(0.04)  #shake
        self.player.combo_count = 0
        self.player.combo_timer = 0.0
        from core import audio
        audio.play("hit_player")




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

    def _handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                self._handle_keydown(event.key)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                self._handle_mouse_down(event.button, event.pos)
            elif event.type == pygame.MOUSEBUTTONUP:          # NEW
                self._handle_mouse_up(event.button, event.pos)    
            elif event.type == pygame.MOUSEMOTION:
                self._handle_mouse_motion(event.pos)

    def _trigger_hitstop(self, duration: float):
    # Cap so a burst of hits can't lock the game.
        self._hitstop_timer = min(0.12, max(self._hitstop_timer, duration))           

    def _handle_mouse_down(self, button, pos):
        if self.inventory_ui.visible:
            self.inventory_ui.handle_mouse_down(button, pos, self.player)
            return

        if (self.evolution_banner.visible or self.profile_screen.visible
        or self.gateway_menu.visible or self.inventory_ui.visible):
            return

        if not self.player.alive:
            return

        world_pos = self.camera.screen_to_world(pos)

        if button == 1:  # left click = walk
            self._move_button_held = True
            self.player.set_move_target(*world_pos)
        elif button == 3:  # right click = attack toward cursor
            self._attack_button_held = True
            self.player.face_toward(*world_pos)
            self._try_attack()

    def _handle_mouse_motion(self, pos):
        if self.inventory_ui.visible:
            self.inventory_ui.handle_mouse_motion(pos, self.player)
            return

        if not self._move_button_held or not self.player.alive:
            return
        if (self.evolution_banner.visible or self.profile_screen.visible
        or self.gateway_menu.visible or self.inventory_ui.visible):
            return

        world_pos = self.camera.screen_to_world(pos)
        self.player.set_move_target(*world_pos)

    def _handle_mouse_up(self, button, pos):
        if button == 1:
            self._move_button_held = False
            if self.player.alive:
                self.player.clear_move_target()
        elif button == 3:                             # NEW
            self._attack_button_held = False      

    def _handle_keydown(self, key):
        mods = pygame.key.get_mods()
        shift_held = mods & pygame.KMOD_SHIFT

        if self.gateway_menu.visible:
            if key == pygame.K_UP:
                self.gateway_menu.move_selection(-1)
            elif key == pygame.K_DOWN:
                self.gateway_menu.move_selection(1)
            elif key in (pygame.K_RETURN, pygame.K_SPACE):
                target = self.gateway_menu.confirm_selection()
                if target:
                    self._handle_gateway_menu_select(target)
            elif key == pygame.K_e:
                #
                self.evolution_banner.cycle_overcharge()
            elif key == pygame.K_o:                          # NEW
                #self.evolution_banner.cycle_overcharge()
                self._try_interact_shrine()
            elif key == pygame.K_ESCAPE:
                self.gateway_menu.hide()
            elif key == pygame.K_LSHIFT:
                self.player.face_toward(*self.camera.screen_to_world(pygame.mouse.get_pos()))
                self.player.try_dash()
            return

        if self.profile_screen.visible:
            if key in (pygame.K_F6, pygame.K_ESCAPE):
                self.profile_screen.hide()
            elif key == pygame.K_TAB:                        # NEW
                self.profile_screen.toggle_view()
            return

        if self.evolution_banner.visible:
            if key in (pygame.K_SPACE, pygame.K_RETURN):
                self._accept_pending_extension()
            elif key == pygame.K_r:
                self._reroll_pending_extension()
            elif key == pygame.K_o:
                 self.evolution_banner.cycle_overcharge()
                 self._refresh_evolution_banner()
            elif key == pygame.K_ESCAPE:
                self._discard_pending_extension()
            return
            

        # --- Inventory/stash panel intercepts navigation keys while open ---
        if self.inventory_ui.visible:
            if key == pygame.K_i:
                self.inventory_ui.toggle()
                return
            if key == pygame.K_b:
                self.inventory_ui.toggle_stash()
                return
            if key == pygame.K_ESCAPE:
                self.inventory_ui.toggle()
                return
            if key == pygame.K_TAB:
                self.inventory_ui.cycle_focus()
                return
            if key == pygame.K_UP:
                self.inventory_ui.move_cursor(0, -1, self.player)
                return
            if key == pygame.K_DOWN:
                self.inventory_ui.move_cursor(0, 1, self.player)
                return
            if key == pygame.K_LEFT:
                self.inventory_ui.move_cursor(-1, 0, self.player)
                return
            if key == pygame.K_RIGHT:
                self.inventory_ui.move_cursor(1, 0, self.player)
                return
            if key == pygame.K_e:
                msg = self.inventory_ui.do_primary_action(self.player)
                if msg:
                    self._show_message(msg)
                return
            if key == pygame.K_r:
                msg = self.inventory_ui.do_stash_action(self.player)
                if msg:
                    self._show_message(msg)
                return
            return

        if key == pygame.K_ESCAPE:
            self.running = False
        elif key == pygame.K_SPACE:
            self._try_attack()
        elif key == pygame.K_i:
            self.inventory_ui.toggle()
        elif key == pygame.K_b:
            self.inventory_ui.toggle_stash()
        elif key == pygame.K_e:
             self._try_interact_shrine()
        elif key == pygame.K_TAB:
            self._open_gateway_menu()
        elif key == pygame.K_LSHIFT:
            k = pygame.key.get_pressed()
            moving = any(k[x] for x in (pygame.K_w, pygame.K_a, pygame.K_s, pygame.K_d,
                                        pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT))
            if not moving:
                self.player.face_toward(*self.camera.screen_to_world(pygame.mouse.get_pos()))
            self.player.try_dash()
        elif key == pygame.K_F9:
            self._save_game()
        elif key == pygame.K_F10:
            self._load_game()
        elif key == pygame.K_F1:
            self.player.gain_xp(50)
        elif key == pygame.K_F2:
            self._debug_spawn_item()
        elif key == pygame.K_F3:
            self._debug_spawn_item(force_rarity="rare")
        elif key == pygame.K_F4: self.show_region_details = not self.show_region_details
        elif key == pygame.K_F5:
            self._debug_spawn_item(force_affix_counts=(3, 3))
        elif key == pygame.K_F6:
            self._show_profile_screen()
        elif key == pygame.K_F7:
            self._debug_print_world_info()
        elif key == pygame.K_F8:
            self._show_message(f"World Seed: {self.world.world_seed}  |  "
                               f"Time since last ext: {self.world.seconds_since_last_extension():.0f}s")
        elif key == pygame.K_F11:
            mode = config.EXTENSION_MODE_DEBUG if shift_held else config.EXTENSION_MODE_MANUAL
            self._generate_next_extension(mode=mode)
        elif key == pygame.K_F12:
            self._debug_spawn_item(force_affix_counts=(1, 1), item_level_override=999)

    # ---------------- profile / history -----------------
    def _current_profile(self):
        return build_player_profile(self.player, self.event_log, self.world, self.exploration)

    def _show_profile_screen(self):
        self.profile_screen.show(self._current_profile(),codex=self.codex)



    def _try_interact_shrine(self) -> bool:
        for shrine in self.current_area.shrines:
            if shrine.area_id != self.current_area_id:
                continue
            if not shrine.player_in_range(self.player):
                continue
            msg = shrine.interact(self)
            if msg:
                self._show_message(msg, duration=3.5)
            return True
        return False

    def _apply_shrine_persistence(self):
        for shrine in self.current_area.shrines:
            if shrine.unique_id() in self.world.used_shrines:
                shrine.used = True    



    # ---------------- world / extensions -----------------
    def _generate_next_extension(self, mode=config.EXTENSION_MODE_MANUAL):
        """
        Two-phase: STAGE a spec and show the offer banner. The world is
        not mutated until the player accepts (_accept_pending_extension).

        mode: 'manual' (player-triggered), 'automatic' (playtime threshold),
        'debug' (forced, bypasses the playtime gate but NOT validation).

        The gate that WILL open is previewed here so the banner can name
        the area it appears in. It is only actually opened on accept. Safe
        to preview because the world is paused while the banner is visible,
        so the closed-gate set cannot change between offer and accept.
        """
        if self._pending_extension_spec is not None:
            return
        debug = (mode == config.EXTENSION_MODE_DEBUG)
        if not debug and self.player.extension_stones <= 0:
            self._show_message("No Extension Stones left. Bosses and elites may drop more.")
            return
        preview_gate = self._pick_gate(debug)
        if preview_gate is None:
            self._show_message(self._locked_hint(), duration=3.0)
            return

        existing_ids = {ext.extension_id for ext in self.world.extensions}
        rng = self.world.rng_service.get_stream("extension_selection")
        profile = self._current_profile()
        spec = self.extension_generator.generate(profile, existing_ids, rng)
        #preview_gate = self.world.pick_next_gate(gate_rng)

        self._pending_extension_spec = spec
        self._pending_gate = preview_gate
        self._pending_evolution_mode = mode
        self._pending_rerolls_left = config.REROLL_CAP
        self._refresh_evolution_banner()

    def _refresh_evolution_banner(self):
        spec = self._pending_extension_spec
        gate = self._pending_gate
        debug = (self._pending_evolution_mode == config.EXTENSION_MODE_DEBUG)

        if debug:
            max_oc = WorldEvolutionBanner.MAX_OVERCHARGE
        else:
            max_oc = min(WorldEvolutionBanner.MAX_OVERCHARGE,
                         max(0, self.player.extension_stones - 1))

        # Clamp so the banner never advertises an overcharge the player
        # cannot afford (non-debug) or exceeds the hard cap (debug).
        oc = min(self.evolution_banner.overcharge_level, max_oc)
        preview_level = spec.area_level + oc * 3
        rule_names = [m["name"] for m in spec.world_modifiers]
        if oc > 0:
            rule_names.append(f"Overcharged Reality (+{oc})")

        self.evolution_banner.show(
            extension_name=spec.extension_name,
            area_level=preview_level,
            is_anomaly=spec.is_anomaly or oc > 0,
            rule_names=rule_names,
            gate_area_name=self._area_display_name(gate.owner_area_id) if gate else None,
            reason=self._evolution_reason(self._current_profile()),
            rerolls_left=self._pending_rerolls_left,
            max_overcharge=max_oc,
        )


    def _accept_pending_extension(self):
        spec = self._pending_extension_spec
        gate = self._pending_gate
        if spec is None:
            self.evolution_banner.dismiss()
            return

        if gate is None:
            self._pending_extension_spec = None
            self._pending_gate = None
            self._pending_evolution_mode = None
            self.evolution_banner.dismiss()
            self._show_message("The world has no sealed door left to open.", duration=3.0)
            return

        overcharge = self.evolution_banner.overcharge_level
        stones_to_spend = 1 + overcharge
        debug = (self._pending_evolution_mode == config.EXTENSION_MODE_DEBUG)

        if not debug:
            if self.player.extension_stones < stones_to_spend:
                overcharge = max(0, self.player.extension_stones - 1)
                stones_to_spend = 1 + overcharge

        if overcharge > 0:
            self._apply_overcharge_to_spec(spec, overcharge)
            spec.overcharge_level = overcharge
            
            from ai.validators import WorldValidator, ProgressionValidator
            existing_ids = {ext.extension_id for ext in self.world.extensions}
            ok, errs = WorldValidator.validate(spec, existing_ids,
                                               self.player.progression.level)
            p_ok, p_errs = ProgressionValidator.validate(spec, self.player.progression.level)
            if not ok or not p_ok:
                print(f"[Game] Overcharged spec rejected: {errs} {p_errs}")
                self._show_message("The overcharged gate collapses -- too unstable.",
                                   duration=3.0)
                self.evolution_banner.dismiss()
                self._pending_extension_spec = None
                self._pending_gate = None
                self._pending_evolution_mode = None
                self.evolution_banner.overcharge_level = 0
                return

        extension = self.world.add_extension(spec)

        if not debug:
            self.player.extension_stones -= stones_to_spend

        opened_gate = self.world.open_gate_to(gate, spec.extension_id)
        if opened_gate is not None:
            opened_gate.overcharge_level = overcharge

        attach_extension(self.current_area, extension, gate,
                         self.world.rng_service, self.world)

        # NEW: codex hook for overcharge
        if overcharge > 0:
            newly = self.codex.record("overcharge_entered", amount=1)
            self._handle_codex_unlocks(newly)

        self._apply_shrine_persistence()
        
        self._on_map_expanded(spec.extension_id)

        self.packs.assign_new() 

        self.event_bus.emit("extension_generated", extension_id=spec.extension_id,
                            mode=self._pending_evolution_mode,
                            area_level=spec.area_level)

        self._pending_extension_spec = None
        self._pending_gate = None
        self._pending_evolution_mode = None
        self._pending_rerolls_left = 0
        self.evolution_banner.dismiss()

        self.evolution_banner.overcharge_level = 0
        #self.evolution_banner.dismiss()

        if opened_gate:
            self._show_message(
                f"A gate has opened in "
                f"{self._area_display_name(opened_gate.owner_area_id)}: "
                f"{spec.extension_name}", duration=3.0)
        else:
            self._show_message(f"The world shifts: {spec.extension_name}", duration=3.0)

    def _reroll_pending_extension(self):
        if self._pending_rerolls_left <= 0:
            self._show_message("No rerolls remaining. Accept or push back.", duration=2.0)
            return

        self._pending_rerolls_left -= 1

        existing_ids = {ext.extension_id for ext in self.world.extensions}
        rng = self.world.rng_service.get_stream("extension_selection")
        profile = self._current_profile()
        spec = self.extension_generator.generate(profile, existing_ids, rng)

        #gate_rng = self.world.rng_service.get_stream("gate_selection")
        #preview_gate = self.world.pick_next_gate(gate_rng)
        preview_gate = self._pick_gate(self._pending_evolution_mode == config.EXTENSION_MODE_DEBUG)

        self._pending_extension_spec = spec
        self._pending_gate = preview_gate
        # mode intentionally preserved across rerolls: an automatic offer
        # that gets rerolled is still an automatic offer, and its push-back
        # still has to stamp the playtime clock (see below).
        self._refresh_evolution_banner()

    def _discard_pending_extension(self):
        was_automatic = (self._pending_evolution_mode == config.EXTENSION_MODE_AUTOMATIC)

        self._pending_extension_spec = None
        self._pending_gate = None
        self._pending_evolution_mode = None
        self._pending_rerolls_left = 0
        self.evolution_banner.dismiss()
        self.evolution_banner.overcharge_level = 0

        if was_automatic:
            # Push-back on an automatic trigger must reset the clock, or
            # automatic_generation_due() returns True next frame and the
            # banner re-fires forever. Semantically: the world considered
            # evolving, the player declined, and now it waits another cycle.
            self.world.playtime_at_last_extension = self.world.total_playtime_seconds

        self._show_message("You declined the world's offer. It waits.", duration=3.0)  

    def _area_display_name(self, area_id: str) -> str:
        if area_id == "starting_area":
            return "the Starting Region"
        ext = self.world.get_extension(area_id)
        return ext.spec.extension_name if ext else area_id

    def _check_automatic_extension(self):
        return
        if self.world.automatic_generation_due():
            self._generate_next_extension(mode=config.EXTENSION_MODE_AUTOMATIC)

    # ---------------- gates / travel -----------------
    def _update_current_region(self):
        ts = config.TILE_SIZE
        rid = self.current_area.region_at_tile(int(self.player.x // ts),
                                            int(self.player.y // ts))
        if rid == self.current_area_id:
            return
        self.current_area_id = rid
        self.current_extension_id = None if rid == "starting_area" else rid
        self.event_bus.emit("area_entered", area_id=rid)
        if rid == "starting_area":
            self._show_message("Entering the Starting Region")
        else:
            ext = self.world.get_extension(rid)
            if ext:
                self._show_message(f"Entering {ext.spec.extension_name} "
                                f"(Area Lvl {ext.spec.area_level})", duration=3.0)

    def _gate_unlocked(self, gate):
        """A region's gate is usable only once that region's boss is dead."""
        if gate.owner_area_id == "starting_area":
            return True
        #return not any(b.alive and getattr(b, "region_id", None) == gate.owner_area_id
        #            for b in self.current_area.bosses)
        return not any(b.alive and not getattr(b, "is_nemesis", False)
                       and getattr(b, "region_id", None) == gate.owner_area_id
                       for b in self.current_area.bosses)

    def _pick_gate(self, debug=False):
        rng = self.world.rng_service.get_stream("gate_selection")
        return self.world.pick_next_gate(rng, eligible=None if debug else self._gate_unlocked)

    def _locked_hint(self):
        for g in self.world.closed_gates():
            if not self._gate_unlocked(g):
                return f"Defeat the guardian of {self._area_display_name(g.owner_area_id)} to unseal more gates."
        return "The world has no sealed door left to open."
    

    def _teleport_to_region(self, rid):
        pos = self.current_area.region_start.get(rid)
        if not pos:
            return
        self._clear_player_projectiles()
        self.player.x, self.player.y = pos
        self.player.clear_move_target()
        self._update_current_region()
        self.camera.update(self.player.x, self.player.y)

    def _on_map_expanded(self, rid):
        tm = self.current_area.tilemap
        self.camera.world_w, self.camera.world_h = tm.pixel_size()
        self.exploration.register_region(rid, self.current_area.region_walkable_count(rid))

    def _handle_gateway_menu_select(self, target_area_id):
        self.gateway_menu.hide()
        self._teleport_to_region(target_area_id)

    def _open_gateway_menu(self):
        near = self.nemesis.regions_with_nemesis()

        def warn(rid):
            return f"   !! {' & '.join(near[rid]).upper()} IS NEAR !!" if rid in near else ""

        entries = []
        if self.current_area_id != "starting_area":
            entries.append({"label": "Starting Region" + warn("starting_area"),
                            "target_area_id": "starting_area"})
        for ext in self.world.extensions:
            if ext.extension_id != self.current_area_id:
                entries.append({
                    "label": f"{ext.spec.extension_name}   (Lvl {ext.spec.area_level})"
                             + warn(ext.extension_id),
                    "target_area_id": ext.extension_id})
        if not entries:
            self._show_message("Nothing to travel to yet. Trigger world evolution first.")
            return
        self.gateway_menu.show(entries)

    def _nearest_boss(self, max_dist=700):
        best, best_d = None, max_dist
        for b in self.current_area.bosses:
            if not b.alive:
                continue
            d = math.hypot(b.x - self.player.x, b.y - self.player.y)
            if d < best_d:
                best, best_d = b, d
        return best

    def _active_world_rules(self):
        return self.world.aggregate_rule_set()

    # ---------------- debug helpers -----------------
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

    # ---------------- combat -----------------
    def _live_attack_targets_sil(self):
        targets = [e for e in self.current_area.enemies if e.alive]
        boss = self.current_area.boss
        if boss is not None and boss.alive:
            targets.append(boss)
        return targets

    def _live_attack_targets(self):
        targets = [e for e in self.current_area.enemies if e.alive]
        targets.extend(b for b in self.current_area.bosses if b.alive)
        return targets

    def _apply_player_hit(self, target, world_rules, damage_multiplier=1.0, techniques=None):
        techniques = list(techniques or [])
        pierce = "dash_strike" in techniques
        is_boss = getattr(target, "is_boss", False)
        if is_boss:
            if not self._boss_attempt_active and not getattr(target, "is_nemesis", False):
                self._boss_attempt_active = True
                self._boss_attempt_boss_id = target.name
                self._boss_deaths_this_attempt = 0     
                self.event_bus.emit("boss_attempt_started", boss_id=target.name)
                self._boss_greeting(target)
            dmg = player_attack_enemy(self.player, target, self.event_bus,
                                      world_rules=world_rules,
                                      damage_multiplier=damage_multiplier, pierce=pierce)
            if dmg > 0:
                self._trigger_hitstop(0.06)
            self.camera.shake(magnitude=4, duration=0.12)
            audio.play("hit_boss")
            self.damage_numbers.spawn(target.x, target.y - 40, str(dmg), (255, 215, 90))
            if techniques:
                self.kinetic.trigger(techniques, target)
            if target.alive:
                self.reactions.on_hit(target, dmg, self.player, world_rules)
            if getattr(target, "phase_just_changed", False):
                target.phase_just_changed = False
                self._trigger_phase_transition(target)
            self.event_bus.emit("boss_attempt_damage")
            if not target.alive:
                self._on_boss_killed(target)
        else:
            dmg = player_attack_enemy(self.player, target, self.event_bus,
                                      world_rules=world_rules,
                                      damage_multiplier=damage_multiplier, pierce=pierce)
            self.damage_numbers.spawn(target.x, target.y - 25, str(dmg), (255, 255, 255))
            if techniques:
                self.kinetic.trigger(techniques, target)
            if target.alive:
                self.reactions.on_hit(target, dmg, self.player, world_rules)
            if not target.alive:
                self._on_enemy_killed(target)


    def _trigger_phase_transition(self, boss):
        """0.18s hitstop + hard shake + a deep audio cue + a screen ring.
        Pure feel; no gameplay numbers change. Reuses the 'evolution' tone
        because it's already the deepest synth in core/audio.py."""
        self._trigger_hitstop(0.18)
        self.camera.shake(magnitude=10, duration=0.35)
        from core import audio
        audio.play("evolution")
        self._show_message(
            f"{boss.name} enters Phase {boss.phase_index + 1}!",
            duration=1.6,
        )
        # Reuse the crit-burst ring for a big phase-change shockwave at the boss.
        self.crit_bursts.append({
            "x": boss.x, "y": boss.y,
            "radius": 140,
            "life": 0.55, "max_life": 0.55,
            "color": (255, 90, 200),
        })
        if not getattr(boss, "is_nemesis", False):
            self._say(boss.name, boss_phase_line(boss.phase_index), 3.0)

    def _try_attack(self):
        if not self.player.alive or not self.player.can_attack():
            return

        from combat.attack_patterns import find_arc_targets, spawn_projectile

        profile = self.player.current_attack_profile()
        self.player.start_attack_cooldown(profile)
        world_rules = self._active_world_rules()

        if profile.kind == "projectile":
            proj = spawn_projectile(
                self.player, profile,
                damage_type=self.player.effective_stats.primary_damage_type,
                world_rules=world_rules,
            )
            proj.technique = self.kinetic.classify_ranged(self.player)
            self.player_projectiles.append(proj)
            weapon = self.player.equipped.get("weapon")
            self.event_bus.emit("skill_used", skill="basic_attack", damage=0, target=None,
                                damage_type=self.player.effective_stats.primary_damage_type,
                                weapon_name=weapon.display_name if weapon else None)
            return

        swing_tech = self.kinetic.classify_melee(self.player, profile)
        if swing_tech == "reverse_slash":
            profile = self.kinetic.widen(profile)

        # Melee visual: spawn the arc/cone FX *before* checking hits so a
        # whiffed swing still shows. Previously this call was missing, so
        # player_swings stayed empty and Game._draw_swing_fx was dead code.
        swing_fx.spawn_swing(self.player_swings, self.player.x, self.player.y,
                            self.player.facing, profile)

        targets = self._live_attack_targets()
        hits = find_arc_targets(self.player, targets, profile)
        if not profile.hits_all:
            hits = hits[:1]

        if hits:
            self.player.combo_count += 1
            self.player.combo_timer = 2.0
        combo_mult = 1.0 + min(self.player.combo_count, 10) * 0.06
        for target in hits:
            techs = [swing_tech] if swing_tech else []
            if self.kinetic.is_intercept(target):
                techs.append("intercept")
            self._apply_player_hit(target, world_rules,
                                   profile.damage_multiplier * combo_mult, techs)

    def _update_player_projectiles(self, dt):
        area = self.current_area
        for proj in list(self.player_projectiles):
            proj.update(dt)

            if not proj.alive:
                self.player_projectiles.remove(proj)
                continue
            if area.tilemap.collides_rect(proj.rect):
                self.player_projectiles.remove(proj)
                continue

            consumed = False
            for enemy in list(area.enemies):
                if not enemy.alive or id(enemy) in proj.hit_ids:
                    continue
                if proj.rect.colliderect(enemy.rect):
                    proj.hit_ids.add(id(enemy))
                    techs = self.kinetic.projectile_techniques(proj)
                    self._apply_player_hit(enemy, proj.world_rules, proj.damage_multiplier, techs)
                    if not proj.piercing:
                        if "perfect_range" in techs and not proj.bonus_used:
                            proj.bonus_used = True      # Perfect Range: pierce one extra
                            continue
                        consumed = True
                        break

            if not consumed:
                for boss in list(area.bosses):
                    if not boss.alive or id(boss) in proj.hit_ids:
                        continue
                    if proj.rect.colliderect(boss.rect):
                        proj.hit_ids.add(id(boss))
                        techs = self.kinetic.projectile_techniques(proj)
                        self._apply_player_hit(boss, proj.world_rules, proj.damage_multiplier, techs)
                        if not proj.piercing:
                            if "perfect_range" in techs and not proj.bonus_used:
                                proj.bonus_used = True
                                continue
                            consumed = True
                            break

            if consumed and proj in self.player_projectiles:
                self.player_projectiles.remove(proj)

    def _clear_player_projectiles(self):
        self.player_projectiles.clear()

    def _on_enemy_killed(self, enemy, _depth=0):
        if enemy not in self.current_area.enemies:
            return
        self.current_area.enemies.remove(enemy)

        self.packs.on_member_killed(enemy)
        self.terrain.on_enemy_killed(enemy)

        self.player.gain_xp(enemy.xp_reward)
        self.event_bus.emit("enemy_killed", name=enemy.name)

        newly = self.codex.record("enemy_killed")
        if getattr(enemy, "pack_role", None) == "commander":
            newly += self.codex.record("commander_killed")
        self._handle_codex_unlocks(newly)

        # --- kill juice ---
        self.crit_bursts.append({
            "x": enemy.x, "y": enemy.y, "radius": 32,
            "life": 0.18, "max_life": 0.18,
            "color": (255, 235, 190),
        })
        if enemy.is_elite:
            self.camera.shake(magnitude=4, duration=0.12)

        rules = self._active_world_rules()

        if "explosive" in getattr(enemy, "affixes", ()):
            self.damage_numbers.spawn(enemy.x, enemy.y - 30, "BOOM", (255, 140, 40))
            if self.player.alive and math.hypot(enemy.x - self.player.x,
                                                enemy.y - self.player.y) < 90:
                dmg = max(1, int(enemy.damage * 2))
                if self.player.take_damage(dmg, cause="fire"):
                    self.event_bus.emit("player_hit", source=enemy.name,
                                        damage=dmg, damage_type="fire")

        if getattr(enemy, "nemesis", None):
            self.nemesis.on_killed(enemy)
            return

        if getattr(enemy, "is_add", False):      # boss minions drop nothing
            return

        rng = self.world.rng_service.get_stream("loot")
        
        # --- UPDATED DROP LOGIC START ---
        # Stone (world-extension currency) drop
        if rng.random() < (0.02 if enemy.is_elite else 0.003):
            self.current_area.spawn_pickup(enemy.x + 12, enemy.y, "stone")
            
        # Item / orb drop
        drop_chance = 0.05 if not enemy.is_elite else 0.28
        if rng.random() < drop_chance:
            self.current_area.spawn_loot(enemy.x, enemy.y, area_level=enemy.area_level,
                                        world_rules=rules)
        elif rng.random() < 0.06:
            self.current_area.spawn_orb(enemy.x, enemy.y)
        # --- UPDATED DROP LOGIC END ---
        
        self._resolve_on_kill_effects(enemy, rules, _depth)

    def _resolve_on_kill_effects(self, victim, rules, depth):
        if depth >= MAX_ON_KILL_DEPTH:
            return
        from combat.combat import _enemy_category
        from combat.damage import resolve_damage
        import math
        for eff in rules.on_kill_effects_for(_enemy_category(victim)):
            kind = eff.get("effect")
            if kind == "heal_player":
                s = self.player.effective_stats
                amt = int(s.max_hp * eff.get("percent", 0.05))
                s.hp = min(s.max_hp, s.hp + amt)
                self.damage_numbers.spawn(self.player.x, self.player.y - 30, f"+{amt}", (90, 255, 120))
            elif kind == "explode":
                radius = eff.get("radius", 80)
                dtype = eff.get("damage_type", "fire")
                base = victim.max_hp * eff.get("damage_ratio", 0.3)
                for other in list(self.current_area.enemies):
                    if other is victim or not other.alive:
                        continue
                    if math.hypot(other.x - victim.x, other.y - victim.y) > radius:
                        continue
                    dmg = resolve_damage(base, dtype, None,
                                        target_resistances=other.resistances,
                                        world_rules=rules,
                                        target_category=_enemy_category(other),
                                        source_category="all")
                    other.take_damage(dmg)
                    self.damage_numbers.spawn(other.x, other.y - 25, str(dmg), (255, 150, 60))
                    if not other.alive:
                        self._on_enemy_killed(other, depth + 1)

    def _roll_boss_loot(self, source, level, world_rules, rng,
                    world_boss: bool = False, loot_rules_override=None):
        """
        Shared loot roll for bosses AND nemesis kills.

        Items and stones are probabilistic; the health orb is always dropped
        (design decision: players should never feel a boss fight gave them
        nothing at all, but the big rewards need to be earned by RNG).
        """
        # --- items ---
        slots = list(BOSS_ITEM_DROPS)
        if world_boss:
            slots = [(min(1.0, c + WORLD_BOSS_ITEM_CHANCE_BONUS), a)
                    for c, a in slots]
            slots.append(WORLD_BOSS_EXTRA_ITEM_SLOT)

        total_slots = len(slots)
        for i, (chance, counts) in enumerate(slots):
            if rng.random() >= chance:
                continue
            x_offset = (i - (total_slots - 1) / 2) * 30
            kwargs = {"area_level": level, "world_rules": world_rules,
                    "force_affix_counts": counts}
            if loot_rules_override is not None:
                kwargs["loot_rules_override"] = loot_rules_override
            self.current_area.spawn_loot(source.x + x_offset, source.y, **kwargs)

        # --- stones ---
        stones_dropped = 0
        for base_chance in BOSS_STONE_DROPS:
            chance = base_chance + (WORLD_BOSS_STONE_CHANCE_BONUS if world_boss else 0.0)
            if rng.random() < chance:
                self.current_area.spawn_pickup(
                    source.x + 40 + stones_dropped * 18, source.y + 30, "stone")
                stones_dropped += 1

        # --- health orb: always ---
        self.current_area.spawn_orb(source.x, source.y + 30)

       

    
    def _on_boss_killed(self, boss):

        if getattr(boss, "_death_handled", False):
            return
        boss._death_handled = True
        if getattr(boss, "nemesis", None):
            self.nemesis.on_killed(boss)
            return
        self._say(boss.name, boss_last_words(), 4.0)

        pressure_level = self._pressure_level(rid := getattr(boss, "region_id", "starting_area"))
        newly = self.codex.record("boss_killed")
        
        # Flawless = player has never died during an attempt against THIS boss.
        prior_deaths = sum(
            1 for e in self.event_log.events_of_type("boss_attempt_ended")
            if e.payload.get("boss_id") == boss.name
            and e.payload.get("result") == "death"
        )
        if prior_deaths == 0 and not getattr(boss, "is_nemesis", False):
            newly += self.codex.record("flawless_boss")
            
        if pressure_level >= 4:
            newly += self.codex.record("pressure_kill")
            
        # Survivor of a full run through every phase.
        if boss.phase_index >= max(0, len(boss.template.phases) - 1):
            newly += self.codex.record("survived_full_boss")
            
        self._handle_codex_unlocks(newly)
        # Reset region pressure - guardian is dead
        self.world.region_presence[rid] = 0.0

        self._show_message(f"{boss.name.upper()} HAS FALLEN.", duration=6.0)
        self.event_bus.emit("boss_killed", name=boss.name)
        if self._boss_attempt_active:
            self.event_bus.emit("boss_attempt_ended", result="victory",
                                boss_id=self._boss_attempt_boss_id)
            self._boss_attempt_active = False
            self._boss_attempt_boss_id = None

        # kill its summoned minions
        for e in list(self.current_area.enemies):
            if getattr(e, "is_add", False):
                e.take_damage(10**9)
                self.current_area.enemies.remove(e)

        #rules = self._active_world_rules()
        #rid = getattr(boss, "region_id", "starting_area")
        #ext = self.world.get_extension(rid)
        #lvl = max(self.player.progression.level, ext.spec.area_level if ext else 3) + 2
        #for i, counts in enumerate([(3, 2), (2, 2), (2, 1), (1, 2)]):
        #    self.current_area.spawn_loot(boss.x + (i - 1.5) * 30, boss.y,
        #                                 area_level=lvl, world_rules=rules,
        #                                 force_affix_counts=counts)
        #self.current_area.spawn_orb(boss.x, boss.y + 30)
        #self.current_area.spawn_pickup(boss.x + 40, boss.y + 30, "stone")
        rules = self._active_world_rules()
        rid = getattr(boss, "region_id", "starting_area")
        ext = self.world.get_extension(rid)
        lvl = max(self.player.progression.level, ext.spec.area_level if ext else 3) + 2
        rng = self.world.rng_service.get_stream("boss_loot")
        self._roll_boss_loot(boss, lvl, rules, rng, world_boss=False)

    def _pickup_loot(self):
        px, py = self.player.x, self.player.y
        for drop in list(self.current_area.loot_drops):
            dist = ((drop["x"] - px) ** 2 + (drop["y"] - py) ** 2) ** 0.5
            if dist >= 30:
                continue
            if drop.get("kind") == "heal":
                s = self.player.effective_stats
                if s.hp < s.max_hp:
                    amt = max(1, int(s.max_hp * 0.25))
                    s.hp = min(s.max_hp, s.hp + amt)
                    self.current_area.loot_drops.remove(drop)
                    self.damage_numbers.spawn(px, py - 30, f"+{amt}", (90, 255, 120))
                continue
            if drop.get("kind") == "stone":
                self.player.extension_stones += 1
                self.current_area.loot_drops.remove(drop)
                self._show_message(f"+1 Extension Stone ({self.player.extension_stones})", duration=2.0)
                continue
            if self.player.inventory.add_item(drop["item"]):
                self.current_area.loot_drops.remove(drop)
                item = drop["item"]
                self.event_bus.emit("item_found", item_id=item.item_id, rarity=item.rarity,
                                    is_six_mod=item.is_six_mod())
                tag = item.affix_count_label()
                if item.is_six_mod():
                    self._show_message(f"★★★ EXCEPTIONAL ITEM: {item.display_name} [{tag}] ★★★",
                                    duration=5.0)
                else:
                    self._show_message(f"Picked up {item.display_name} [{tag}]", duration=1.4)
        
    def _save_game(self):
        state = {
            "world": self.world.to_dict(),
            "player": self.player.to_dict(),
            "current_extension_id": self.current_extension_id,
            "event_log": self.event_log.to_dict(),
            "exploration": self.exploration.to_dict(),
             "nemesis": self.nemesis.to_dict(),
             "used_shrines": sorted(self.world.used_shrines),
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

        # --- 1. Reset in-flight / transient state ---------------------------
        self._clear_player_projectiles()
        self._hitstop_timer = 0.0
        self._respawn_timer = 1.5
        self._pending_extension_spec = None
        self._pending_gate = None
        self._pending_evolution_mode = None
        self._pending_rerolls_left = 0
        self._boss_attempt_active = False
        self._boss_attempt_boss_id = None

        # Close any modal UI that happened to be open at save time.
        if self.evolution_banner.visible:
            self.evolution_banner.dismiss()
        if self.profile_screen.visible:
            self.profile_screen.hide()
        if self.gateway_menu.visible:
            self.gateway_menu.hide()
        if self.inventory_ui.visible:
            self.inventory_ui.toggle()

        # --- 2. Restore world & player --------------------------------------
        self.world = World.from_dict(data["world"])        

        #self.player = Player.from_dict(data["player"], self.event_bus)

        self.player = Player.from_dict(data["player"], self.event_bus,
                                        codex_manager=self.codex)

        self.player.stash = self.save_manager.load_stash()

        self.reactions.reset()
        self.dialogue = None
        self.nemesis.load(data.get("nemesis"))

        # --- 3. Restore event log WITHOUT re-subscribing --------------------
        # EventLog.__init__ wires ~15 callbacks into the bus it is handed.
        # Calling EventLog.from_dict(self.event_bus, ...) on every load left
        # the previous instance's callbacks attached to the live bus, so each
        # save/load cycle stacked another ghost logger. Instead: rebuild into
        # a throwaway bus (absorbing the subscriptions) and transplant just
        # the events list onto the one long-lived EventLog instance.
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

        # --- 4. Rebuild the single map and replay expansions in order -------
        self.starting_area = build_level(self.world.rng_service)
        self.current_area = self.starting_area
        self.current_area_id = "starting_area"
        self.current_extension_id = None

        self._apply_shrine_persistence()

        self.exploration.register_area("starting_area", self.starting_area.tilemap)
        self._ensure_starting_gate_registered()
        self._apply_shrine_persistence()
        

        for ext in self.world.extensions:
            gate = next((g for g in self.world.gates
                         if g.target_extension_id == ext.extension_id), None)
            if gate is None:
                continue
            attach_extension(self.current_area, ext, gate,
                             self.world.rng_service, self.world)
            self._on_map_expanded(ext.extension_id)

        self._apply_shrine_persistence()


        self.packs.reset()
        self.terrain.reset()
        self.packs.assign_new()
        
        self.camera = Camera(*self.current_area.tilemap.pixel_size())
        self._update_current_region()
        self._show_message("Game loaded.")

    def _update(self, dt):
        if self.dialogue:
            self.dialogue["t"] -= dt
            if self.dialogue["t"] <= 0:
                self.dialogue = None

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
        or self.gateway_menu.visible or self.inventory_ui.visible):
            return


        # ---- safe room regeneration ----
        if self.player.alive:
            safe = self.starting_area.safe_room_rect
            if safe.collidepoint(self.player.x, self.player.y):
                stats = self.player.effective_stats
                if stats.hp < stats.max_hp:
                    #heal = max(1, int(stats.max_hp * 0.04 * dt))
                    #stats.hp = min(stats.max_hp, stats.hp + heal)
                    self._safe_room_heal_acc += stats.max_hp * 0.04 * dt
                    if self._safe_room_heal_acc >= 1.0:
                        heal = int(self._safe_room_heal_acc)
                        self._safe_room_heal_acc -= heal
                        stats.hp = min(stats.max_hp, stats.hp + heal)

        # ---- shrines tick ----
        for shrine in self.current_area.shrines:
            shrine.update(dt)

        # Moved here from above the modal guard: DoT ticks and telegraphed
        # novas must not resolve while the player is reading a menu or the
        # evolution banner (they were previously visible to nemesis novas).
        self.reactions.tick(dt)
        self.nemesis.update(dt)

        if self.player.alive:
            keys = pygame.key.get_pressed()
            self.player.handle_input(dt, keys, self.current_area.tilemap)
            self.kinetic.update(dt)
            self._pickup_loot()
            self._update_current_region()
            self._update_region_pressure(dt) 
            self._update_roaming_spawns(dt)
            self.exploration.update(dt, self.current_area_id, self.player.x, self.player.y)
            if self._attack_button_held:
                world_pos = self.camera.screen_to_world(pygame.mouse.get_pos())
                self.player.face_toward(*world_pos)
                self._try_attack()
        else:
            self._respawn_timer = getattr(self, "_respawn_timer", 1.5) - dt
            if self._respawn_timer <= 0:
                self._respawn_player()

        world_rules = self._active_world_rules()

        
        # If the tracked boss attempt's boss is dead or the player has
        # wandered far away, close out the attempt so the next boss starts
        # a fresh one.
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

    def _respawn_player(self):
        #self.player.x, self.player.y = self.current_area.start_pos
        self.player.x, self.player.y = self.current_area.region_start.get(
        self.current_area_id, self.current_area.start_pos)
        self.player.alive = True
        self.player.heal_to_full()
        self.terrain.clear_hostile()
        self._respawn_timer = 1.5
        self._show_message("Respawned.")

    def _spawn_boss_adds(self, boss):
        n, boss.summon_requests = boss.summon_requests, 0
        alive_adds = sum(1 for e in self.current_area.enemies
                        if getattr(e, "is_add", False) and e.alive)
        n = min(n, 14 - alive_adds)
        if n <= 0:
            return
        rid = getattr(boss, "region_id", "starting_area")
        ext = self.world.get_extension(rid)
        lvl = ext.spec.area_level if ext else 3
        rng = self.world.rng_service.get_stream("boss_adds")
        tm = self.current_area.tilemap
        for _ in range(n):
            for _try in range(10):
                x = boss.x + rng.randint(-140, 140)
                y = boss.y + rng.randint(-140, 140)
                if tm.collides_rect(pygame.Rect(int(x - 10), int(y - 10), 20, 20)):
                    continue
                e = spawn_basic_enemy(x, y, rng, area_level=lvl, force_behavior="swarmer")
                e.region_id = rid
                e.is_add = True
                e.xp_reward = 3
                self.current_area.enemies.append(e)
                break
        self._show_message("The guardian summons minions!", duration=1.5)    

    def _draw(self):
        self.screen.fill(config.COLOR_BG)

        gates_here = self.world.gates_in_area(self.current_area_id)
        self.current_area.draw(self.screen, self.camera, gates=gates_here)
        self._draw_region_atmosphere()

        self.terrain.draw(self.screen, self.camera)


        # Safe zone tint
        if self.current_area_id == "starting_area":
            safe_scr = self.camera.world_to_screen_rect(self.starting_area.safe_room_rect)
            pygame.draw.rect(self.screen, (70, 160, 90), safe_scr, 2, border_radius=10)
            label = self._info_font.render("SAFE ZONE", True, (120, 220, 150))
            self.screen.blit(label, (safe_scr.centerx - label.get_width() // 2,
                                    safe_scr.top + 6))

        # Shrines
        for shrine in self.current_area.shrines:
            shrine.draw(self.screen, self.camera)
        

        for enemy in self.current_area.enemies:
            if enemy.alive:
                enemy.draw(self.screen, self.camera)

        #if self.current_area.boss is not None and self.current_area.boss.alive:
        #    self.current_area.boss.draw(self.screen, self.camera)

        for boss in self.current_area.bosses:
            if boss.alive:
                boss.draw(self.screen, self.camera)

        self.packs.draw(self.screen, self.camera)
        
        self.reactions.draw(self.screen, self.camera)
       
        

        if self.player.alive:
            self.player.draw(self.screen, self.camera)

        self.nemesis.draw(self.screen, self.camera)

        self._draw_swing_fx()

        

        #boss_for_hud = self.current_area.boss
        boss_for_hud = self._nearest_boss()
        self.damage_numbers.draw(self.screen, self.camera)
        self.hud.draw(self.screen, self.player, boss=boss_for_hud, message=self.message)

        self._draw_dialogue()

        self._draw_player_projectiles()
        self._draw_crit_bursts()
        self.inventory_ui.draw(self.screen, self.player, world_rules=self._active_world_rules())
        self._draw_area_label()
        #self._draw_region_info()
        self.evolution_banner.draw(self.screen)
        self.profile_screen.draw(self.screen)
        self.gateway_menu.draw(self.screen)

        if not self.inventory_ui.visible:
            self._draw_region_info()

      
        

        for shrine in self.current_area.shrines:
            if (shrine.area_id == self.current_area_id
                    and shrine.usable
                    and shrine.player_in_range(self.player)):
                sx, sy = self.camera.world_to_screen((shrine.x, shrine.y))
                hint = self._info_font_b.render(shrine.interaction_hint(), True, (255, 240, 170))
                self.screen.blit(hint, (sx - hint.get_width() // 2, sy - 66))
                break
             
        pygame.display.flip()  


    def _draw_swing_fx(self):
        if not self.player_swings:
            return
        self._fx_surface.fill((0, 0, 0, 0))
        for fx in self.player_swings:
            pts = swing_fx.build_polygon_points(fx, self.camera.world_to_screen)
            frac = fx["life"] / fx["max_life"]
            alpha = int(150 * frac)
            # Greatsword cones get a warmer tint than sword arcs
            fill = (255, 235, 130, alpha) if fx["arc_deg"] >= 150 else (200, 230, 255, alpha)
            pygame.draw.polygon(self._fx_surface, fill, pts)
            pygame.draw.lines(self._fx_surface,
                              (255, 255, 255, min(255, alpha + 80)),
                              True, pts, 2)
        self.screen.blit(self._fx_surface, (0, 0))


    def _draw_crit_bursts(self):
        for b in self.crit_bursts:
            frac = b["life"] / b["max_life"]
            # Expands from 0 -> radius over the lifetime; fades out.
            current_r = b["radius"] * (1.0 - frac)
            if current_r < 2:
                continue
            center = self.camera.world_to_screen((b["x"], b["y"]))
            r, g, bl = b["color"]
            faded = (int(r * frac + 40 * (1 - frac)),
                    int(g * frac + 40 * (1 - frac)),
                    int(bl * frac + 40 * (1 - frac)))
            width = max(1, int(4 * frac))
            pygame.draw.circle(self.screen, faded, center, int(current_r), width)

    def _draw_player_projectiles(self):
            for proj in self.player_projectiles:
                pos = self.camera.world_to_screen((proj.x, proj.y))
                pygame.draw.circle(self.screen, (255, 220, 120), pos, proj.radius)
                pygame.draw.circle(self.screen, (255, 255, 255), pos, proj.radius, 1)

    def _draw_region_info(self):
        rid = self.current_area_id
        ext = self.world.get_extension(rid) if rid != "starting_area" else None
        W = 320
        lines = []   # (text, color, bold)

        if ext is None:
            lines.append(("Starting Region", (255, 255, 255), True))
            lines.append(("Area Lvl 1  -  the original world", (200, 200, 210), False))
        else:
            s = ext.spec
            lines.append((s.extension_name, (255, 255, 255), True))
            tag = "  [ANOMALY]" if s.is_anomaly else ""
            if s.overcharge_level > 0:                       # NEW
                tag += f"  [OVERCHARGE +{s.overcharge_level}]"
            lines.append((f"Area Lvl {s.area_level}{tag}", (255, 120, 120) if s.is_anomaly
                          else (200, 200, 210), False))
            gate = next((g for g in self.world.gates
                         if g.target_extension_id == rid), None)
            if gate:
                lines.append((f"Opened from: {self._area_display_name(gate.owner_area_id)}",
                              (140, 220, 255), False))
            if s.world_modifiers:
                lines.append(("World rules:", (180, 255, 180), False))
                for m in s.world_modifiers:
                    lines.append((f" - {m.get('name', '?')}", (180, 255, 180), False))
                    if self.show_region_details:
                        for w in textwrap.wrap(m.get("description", ""), 40):
                            lines.append((f"    {w}", (140, 190, 140), False))

        alive = sum(1 for e in self.current_area.enemies
                    if e.alive and getattr(e, "region_id", "starting_area") == rid)
        lines.append((f"Enemies left: {alive}", (230, 200, 200), False))
        for b in self.current_area.bosses:
            if getattr(b, "region_id", "starting_area") == rid:
                lines.append((f"Boss: {b.name} ({'alive' if b.alive else 'defeated'})",
                              (220, 150, 240), False))

        if self.show_region_details and ext is not None:
            fav = ext.spec.loot_rules.get("favored_damage_types", {})
            if fav:
                lines.append(("Loot favors: " + ", ".join(fav.keys()), (240, 220, 120), False))
            arch = ext.spec.boss_personalization.get("mechanic_archetype")
            if arch:
                lines.append((f"Boss style: {arch}", (240, 220, 120), False))

        # NEW: pressure
        level = self._pressure_level(rid)
        if level > 0:
            label = self._pressure_label(level)
            #color = self.PRESSURE_COLORS[level]
            color = PRESSURE_COLORS[level]
            lines.append((f"World Pressure: {label}", color, level >= 2))

        lines.append((f"Explored {self.exploration.fraction_for(rid) * 100:.0f}%   "
                      f"(F4: {'less' if self.show_region_details else 'more'})",
                      (150, 150, 160), False))

        h = 16 + sum(22 if b else 18 for _, _, b in lines)
        panel = pygame.Surface((W, h), pygame.SRCALPHA)
        panel.fill((10, 10, 16, 190))
        self.screen.blit(panel, (config.SCREEN_WIDTH - W - 12, 12))
        y = 20
        for text, color, bold in lines:
            font = self._info_font_b if bold else self._info_font
            self.screen.blit(font.render(text, True, color),
                           (config.SCREEN_WIDTH - W - 4, y))
            y += 22 if bold else 18
    
    def _draw_area_label(self):
        font = pygame.font.SysFont("consolas", 18, bold=True)
        if self.current_extension_id is None:
            label = "Starting Region  (TAB: Gateway Portal | F6: Profile | F11: Generate Extension)"
        else:
            ext = self.world.get_extension(self.current_extension_id)
            label = f"{ext.spec.extension_name}  |  Area Lvl {ext.spec.area_level}  (TAB: Gateway Portal)"
        explored = self.exploration.fraction_for(self.current_area_id) * 100
        label += f"  |  Explored {explored:.0f}%"    
        text = font.render(label, True, (220, 220, 230))
        self.screen.blit(text, (config.SCREEN_WIDTH // 2 - text.get_width() // 2, config.SCREEN_HEIGHT - 24))
        if self.player.combo_count >= 2:
            font = pygame.font.SysFont("consolas", 22, bold=True)
            pct = int(min(self.player.combo_count, 10) * 6)
            txt = font.render(f"COMBO x{self.player.combo_count}  (+{pct}%)",
                            True, (255, 210, 90))
            self.screen.blit(txt, (config.SCREEN_WIDTH // 2 - txt.get_width() // 2, 96))

    def _on_player_death(self, cause=None):
        if self._boss_attempt_active:                    # NEW
            self._boss_deaths_this_attempt += 1
        lost = self.player.progression.lose_xp_progress(0.10)  # tune: 0.10 = 10%
        msg = "You died. Respawning..."
        if lost > 0:
            msg = f"You died (-{lost} XP progress). Respawning..."
        self._show_message(msg)
        if self._boss_attempt_active:
            b = self._nearest_boss(1500)
            if b:
                self._say(b.name, boss_death_taunt(self._current_profile().main_damage))
            self.event_bus.emit("boss_attempt_ended", result="death", boss_id=self._boss_attempt_boss_id)
            self._boss_attempt_active = False
            self._boss_attempt_boss_id = None
        self.nemesis.on_player_death(cause) 
    
    def _draw_region_atmosphere(self):
        """Translucent color wash so each extension feels visually distinct."""
        if self.current_extension_id is None:
            return
        ext = self.world.get_extension(self.current_extension_id)
        if ext is None:
            return
        tint = self._region_tint(ext.spec)
        if tint is None:
            return
        overlay = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        overlay.set_alpha(30)
        overlay.fill(tint)
        self.screen.blit(overlay, (0, 0))

    def _region_tint(self, spec) -> tuple | None:
        flavor = " ".join(m.get("name", "") for m in spec.world_modifiers).lower()
        if "chaos" in flavor or "corrupt" in flavor:
            return (120, 40, 160)
        if "storm" in flavor or "conductive" in flavor:
            return (40, 90, 170)
        if "ash" in flavor or "fire" in flavor:
            return (170, 70, 30)
        if "frozen" in flavor or "cold" in flavor or "glacier" in flavor:
            return (60, 120, 160)
        if "bone" in flavor or "brittle" in flavor:
            return (150, 140, 110)
        if spec.is_anomaly:
            return (180, 40, 60)
        return None

    def _evolution_reason(self, profile) -> str:
        from ai.personalization import derive_hints
        hints = derive_hints(profile)
        if hints.boss_struggling:
            return "The world noticed your struggles against bosses."
        if hints.weak_defense:
            return "The world noticed your defenses are thin."
        if hints.frequent_death_causes:
            dtype = hints.frequent_death_causes[0]
            return f"{dtype.title()} has killed you often. The world remembers."
        if hints.under_explored:
            return "You rush through regions. The world reshapes itself."
        return f"The world has been watching your {hints.dominant_damage_type} build."



    def _pressure_level(self, rid):
        t = self.world.region_presence.get(rid, 0.0)
        level = 0
        for i, thresh in enumerate(PRESSURE_THRESHOLDS):
            if t >= thresh:
                level = i
        return level

    def _update_region_pressure(self, dt):
        rid = self.current_extension_id
        if rid is None:
            return
        ext = self.world.get_extension(rid)
        if ext is None:
            return
        boss_alive = any(b.alive and getattr(b, "region_id", None) == rid
                        for b in self.current_area.bosses)
        if not boss_alive:
            return                                  # guardian dead → pressure frozen
        self.world.region_presence[rid] = self.world.region_presence.get(rid, 0.0) + dt
        level = self._pressure_level(rid)

        if level >= 3:
            self._pressure_ambush_timer -= dt
            if self._pressure_ambush_timer <= 0:
                self._pressure_ambush_timer = max(20.0, 45.0 - (level - 3) * 10.0)
                self._spawn_pressure_ambush(rid, ext, level)

        if level >= 4:
            self._pressure_hazard_timer -= dt
            if self._pressure_hazard_timer <= 0:
                self._pressure_hazard_timer = 8.0
                self._apply_pressure_hazard(rid)

    def _spawn_pressure_ambush(self, rid, ext, level):
        from enemies.enemy import spawn_basic_enemy
        rng = self.world.rng_service.get_stream(f"pressure_ambush:{rid}")
        count = min(4, 1 + (level - 3))
        for _ in range(count):
            for _try in range(20):
                angle = rng.uniform(0, math.tau)
                dist = rng.uniform(400, 700)
                x = self.player.x + math.cos(angle) * dist
                y = self.player.y + math.sin(angle) * dist
                if self.current_area.tilemap.collides_rect(
                        pygame.Rect(int(x - 14), int(y - 14), 28, 28)):
                    continue
                e = spawn_basic_enemy(x, y, rng, area_level=ext.spec.area_level)
                e.region_id = rid
                e.is_elite = True
                e.max_hp = int(e.max_hp * 1.5)
                e.hp = e.max_hp
                e.name = "Ambushing " + e.name
                self.current_area.enemies.append(e)
                break
        self._show_message(
            f"The region's pressure draws an ambush! [{self._pressure_label(level)}]",
            duration=2.5)

    def _apply_pressure_hazard(self, rid):
        if not self.player.alive:
            return
        from combat.damage import resolve_damage
        stats = self.player.effective_stats
        base = max(3, int(stats.max_hp * 0.03))
        res = dict(stats.resistances)
        res["armor_flat"] = stats.armor
        dmg = resolve_damage(base, "chaos", None, target_resistances=res,
                            world_rules=self._active_world_rules(),
                            target_category="player", source_category="elite")
        if self.player.take_damage(dmg, cause="chaos"):
            self.damage_numbers.spawn(self.player.x, self.player.y - 30,
                                    str(dmg), (200, 90, 255))
            self.event_bus.emit("player_hit", source="the corrupted world",
                                damage=dmg, damage_type="chaos")

    #def _pressure_label(self, level):
    #    return self.PRESSURE_LABELS[min(level, len(self.PRESSURE_LABELS) - 1)]  



    def _handle_codex_unlocks(self, newly):
        for aid in newly:
            ach = self.codex.achievement_info(aid)
            self._show_message(
                f"CODEX UNLOCKED: {ach.get('name', aid)}!",
                duration=4.0) 
        if newly and self.player is not None:
            self.player.recalc_stats()

    def _pressure_label(self, level):
        return PRESSURE_LABELS[min(level, len(PRESSURE_LABELS) - 1)]