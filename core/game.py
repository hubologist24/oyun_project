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
import pygame

import core.config as config
from core.rng import RNGService
from core.events import EventBus
from player.player import Player
from world.level import build_level, AreaInstance
from world.world import World
from world.extension_generator import WorldExtensionGenerator
from world.camera import Camera
from combat.combat import player_attack_enemy
from items.item_generator import generate_random_item
from save.save_manager import SaveManager
from ui.hud import HUD, InventoryUI
from ui.world_evolution import WorldEvolutionBanner
from ui.profile_screen import ProfileScreen
from ui.gateway_menu import GatewayMenu
from history.event_log import EventLog
from history.exploration import ExplorationTracker
from history.aggregator import build_player_profile


class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("AI-Evolving ARPG - Phase 8 Prototype (Gates & Gateway Portal)")
        self.screen = pygame.display.set_mode((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        self.clock = pygame.time.Clock()
        self.running = True

        self.event_bus = EventBus()
        self.save_manager = SaveManager()

        self.event_log = EventLog(self.event_bus)
        self.exploration = ExplorationTracker()
        self.extension_generator = self._build_extension_generator()

        world_seed = random.randint(0, 2**31 - 1)
        self.world = World(world_seed)

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

        self._boss_attempt_active = False
        self._boss_attempt_boss_id = None

        self._register_events()

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
        """
        Idempotent: registers the starting area's single reserved
        closed gate exactly once. Safe to call both on fresh-world
        __init__ AND after a load, in case the loaded save predates
        the gate system or otherwise ended up with zero gates.
        """
        if self.world.gates_in_area("starting_area"):
            return
        gx0, gy0, gx1, gy1 = self.starting_area.gate_room
        tile_col, tile_row = (gx0 + gx1) // 2, (gy0 + gy1) // 2
        self.world.register_starting_gate(room_index=0, tile_col=tile_col, tile_row=tile_row)

    def _register_events(self):
        self.event_bus.subscribe("player_death", self._on_player_death)
        self.event_bus.subscribe("level_up", lambda level: self._show_message(f"Level Up! Now level {level}"))

    def _on_player_death(self, cause=None):
        self._show_message("You died. Respawning...")
        if self._boss_attempt_active:
            self.event_bus.emit("boss_attempt_ended", result="death", boss_id=self._boss_attempt_boss_id)
            self._boss_attempt_active = False
            self._boss_attempt_boss_id = None

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
            elif event.type == pygame.MOUSEMOTION:
                self._handle_mouse_motion(event.pos)

    def _handle_mouse_down(self, button, pos):
        if self.inventory_ui.visible:
            self.inventory_ui.handle_mouse_down(button, pos, self.player)
            return

        if self.evolution_banner.visible or self.profile_screen.visible or self.gateway_menu.visible:
            return

        if not self.player.alive:
            return

        world_pos = self.camera.screen_to_world(pos)

        if button == 1:  # left click = walk
            self.player.set_move_target(*world_pos)
        elif button == 3:  # right click = attack toward cursor
            self.player.face_toward(*world_pos)
            self._try_attack()

    def _handle_mouse_motion(self, pos):
        if self.inventory_ui.visible:
            self.inventory_ui.handle_mouse_motion(pos, self.player)

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
            elif key == pygame.K_ESCAPE:
                self.gateway_menu.hide()
            return

        if self.profile_screen.visible:
            if key in (pygame.K_F6, pygame.K_ESCAPE):
                self.profile_screen.hide()
            return

        if self.evolution_banner.visible:
            if key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_ESCAPE):
                self.evolution_banner.dismiss()
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
        elif key == pygame.K_TAB:
            self._open_gateway_menu()
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
        self.profile_screen.show(self._current_profile())

    # ---------------- world / extensions -----------------
    def _generate_next_extension(self, mode=config.EXTENSION_MODE_MANUAL):
        """
        mode: 'manual' (player-triggered), 'automatic' (playtime
        threshold), 'debug' (forced, bypasses the playtime gate, but
        NOT validation -- debug never means unsafe).

        World Evolution: generates+validates a new extension, then
        opens an existing closed gate somewhere in the already-explored
        world and links it to the new extension.
        """
        existing_ids = {ext.extension_id for ext in self.world.extensions}
        rng = self.world.rng_service.get_stream("extension_selection")
        profile = self._current_profile()

        spec = self.extension_generator.generate(profile, existing_ids, rng)

        extension = self.world.add_extension(spec)
        extension.ensure_built(self.world.rng_service, world=self.world)

        gate_rng = self.world.rng_service.get_stream("gate_selection")
        opened_gate = self.world.open_next_gate_to(spec.extension_id, gate_rng)

        self.evolution_banner.show(
            extension_name=spec.extension_name,
            area_level=spec.area_level,
            is_anomaly=spec.is_anomaly,
            rule_names=[m["name"] for m in spec.world_modifiers],
            gate_area_name=self._area_display_name(opened_gate.owner_area_id) if opened_gate else None,
        )
        self.event_bus.emit("extension_generated", extension_id=spec.extension_id,
                            mode=mode, area_level=spec.area_level)
        self._show_message(f"A gate has opened: {spec.extension_name}", duration=3.0)

    def _area_display_name(self, area_id: str) -> str:
        if area_id == "starting_area":
            return "the Starting Region"
        ext = self.world.get_extension(area_id)
        return ext.spec.extension_name if ext else area_id

    def _check_automatic_extension(self):
        if self.world.automatic_generation_due():
            self._generate_next_extension(mode=config.EXTENSION_MODE_AUTOMATIC)

    # ---------------- gates / travel -----------------
    def _check_gate_interactions(self):
        """
        Walking onto an OPEN gate's tile teleports the player to that
        gate's target extension's start position. Standing on a CLOSED
        gate does nothing (visually distinct -- drawn dim/locked).
        """
        gates_here = self.world.gates_in_area(self.current_area_id)
        if not gates_here:
            return
        ts = config.TILE_SIZE
        player_col, player_row = int(self.player.x // ts), int(self.player.y // ts)

        for gate in gates_here:
            if gate.tile_col == player_col and gate.tile_row == player_row and gate.is_open():
                self._travel_via_gate(gate)
                return

    def _travel_via_gate(self, gate):
        target_id = gate.target_extension_id
        if target_id == "starting_area":
            self._enter_starting_area()
        else:
            ext = self.world.get_extension(target_id)
            if ext is None:
                return
            ext.ensure_built(self.world.rng_service, world=self.world)
            self._enter_extension(ext)

    def _enter_starting_area(self):
        self.current_area = self.starting_area
        self.current_extension_id = None
        self.current_area_id = "starting_area"
        self.player.x, self.player.y = self.starting_area.start_pos
        self.camera = Camera(*self.starting_area.tilemap.pixel_size())
        self.exploration.register_area(self.current_area_id, self.current_area.tilemap)
        self.event_bus.emit("area_entered", area_id=self.current_area_id)
        self._show_message("Returned to the Starting Region.")

    def _enter_extension(self, extension):
        stream = self.world.rng_service.derive_child("world", f"extension:{extension.extension_id}:runtime")
        self.current_area = AreaInstance(extension.area, stream, extension)
        self.current_extension_id = extension.extension_id
        self.current_area_id = extension.extension_id
        self.exploration.register_area(self.current_area_id, self.current_area.tilemap)
        self.player.x, self.player.y = self.current_area.start_pos
        self.camera = Camera(*self.current_area.tilemap.pixel_size())
        self.event_bus.emit("area_entered", area_id=self.current_area_id)
        self._show_message(f"Traveled to {extension.spec.extension_name}")

    def _open_gateway_menu(self):
        open_gates = self.world.open_gates()
        entries = []
        if self.current_area_id != "starting_area":
            entries.append({"label": "Starting Region", "target_area_id": "starting_area"})
        for gate in open_gates:
            ext = self.world.get_extension(gate.target_extension_id)
            if ext and ext.extension_id != self.current_area_id:
                entries.append({"label": ext.spec.extension_name, "target_area_id": ext.extension_id})
        if not entries:
            self._show_message("No open gates yet. Explore and trigger world evolution first.")
            return
        self.gateway_menu.show(entries)

    def _handle_gateway_menu_select(self, target_area_id):
        self.gateway_menu.hide()
        if target_area_id == "starting_area":
            self._enter_starting_area()
        else:
            ext = self.world.get_extension(target_area_id)
            if ext:
                ext.ensure_built(self.world.rng_service, world=self.world)
                self._enter_extension(ext)

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
    def _try_attack(self):
        if not self.player.alive or not self.player.can_attack():
            return
        self.player.start_attack_cooldown()
        hitbox = self.player.attack_hitbox()
        world_rules = self._active_world_rules()

        for enemy in list(self.current_area.enemies):
            if enemy.alive and hitbox.colliderect(enemy.rect):
                player_attack_enemy(self.player, enemy, self.event_bus, world_rules=world_rules)
                if not enemy.alive:
                    self._on_enemy_killed(enemy)

        boss = self.current_area.boss
        if boss is not None and boss.alive and hitbox.colliderect(boss.rect):
            if not self._boss_attempt_active:
                self._boss_attempt_active = True
                self._boss_attempt_boss_id = boss.name
                self.event_bus.emit("boss_attempt_started", boss_id=boss.name)

            player_attack_enemy(self.player, boss, self.event_bus, world_rules=world_rules)
            self.event_bus.emit("boss_attempt_damage")
            if not boss.alive:
                self._on_boss_killed(boss)

    def _on_enemy_killed(self, enemy):
        self.current_area.enemies.remove(enemy)
        self.player.gain_xp(enemy.xp_reward)
        self.event_bus.emit("enemy_killed", name=enemy.name)
        drop_chance = 0.35 if not enemy.is_elite else 0.9
        if self.world.rng_service.get_stream("loot").random() < drop_chance:
            self.current_area.spawn_loot(enemy.x, enemy.y, area_level=enemy.area_level,
                                         world_rules=self._active_world_rules())

    def _on_boss_killed(self, boss):
        self._show_message(f"{boss.name.upper()} HAS FALLEN.", duration=6.0)
        self.event_bus.emit("boss_killed", name=boss.name)
        if self._boss_attempt_active:
            self.event_bus.emit("boss_attempt_ended", result="victory", boss_id=self._boss_attempt_boss_id)
            self._boss_attempt_active = False
            self._boss_attempt_boss_id = None

    def _pickup_loot(self):
        px, py = self.player.x, self.player.y
        for drop in list(self.current_area.loot_drops):
            dist = ((drop["x"] - px) ** 2 + (drop["y"] - py) ** 2) ** 0.5
            if dist < 30:
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
        self.world = World.from_dict(data["world"])
        self.player = Player.from_dict(data["player"], self.event_bus)
        self.player.stash = self.save_manager.load_stash()

        if "event_log" in data:
            self.event_log = EventLog.from_dict(data["event_log"], self.event_bus)
        else:
            self.event_log = EventLog(self.event_bus)

        if "exploration" in data:
            self.exploration = ExplorationTracker.from_dict(data["exploration"])
        else:
            self.exploration = ExplorationTracker()

        target_ext_id = data.get("current_extension_id")
        if target_ext_id:
            ext = self.world.get_extension(target_ext_id)
            if ext:
                ext.ensure_built(self.world.rng_service, world=self.world)
                stream = self.world.rng_service.derive_child("world", f"extension:{ext.extension_id}:runtime")
                self.current_area = AreaInstance(ext.area, stream, ext)
                self.current_extension_id = ext.extension_id
                self.current_area_id = ext.extension_id
                self.camera = Camera(*self.current_area.tilemap.pixel_size())
        else:
            self.starting_area = build_level(self.world.rng_service)
            self.current_area = self.starting_area
            self.current_extension_id = None
            self.current_area_id = "starting_area"
            self.camera = Camera(*self.starting_area.tilemap.pixel_size())

        # Defensive: guarantees a starting gate exists even if this save
        # predates the gate system or World.from_dict() restored zero gates.
        self._ensure_starting_gate_registered()

        self.exploration.register_area(self.current_area_id, self.current_area.tilemap)
        self._show_message("Game loaded.")

    def _update(self, dt):
        if self._message_timer > 0:
            self._message_timer -= dt
            if self._message_timer <= 0:
                self.message = ""

        self.world.total_playtime_seconds += dt

        if self.evolution_banner.visible or self.profile_screen.visible or self.gateway_menu.visible:
            return

        if self.player.alive:
            keys = pygame.key.get_pressed()
            self.player.handle_input(dt, keys, self.current_area.tilemap)
            self._pickup_loot()
            self._check_gate_interactions()
            self.exploration.update(dt, self.current_area_id, self.player.x, self.player.y)
        else:
            self._respawn_timer = getattr(self, "_respawn_timer", 1.5) - dt
            if self._respawn_timer <= 0:
                self._respawn_player()

        world_rules = self._active_world_rules()

        for enemy in self.current_area.enemies:
            enemy.update(dt, self.player, self.current_area.tilemap, self.event_bus)

        if self.current_area.boss is not None and self.current_area.boss.alive and self.player.alive:
            self.current_area.boss.update(dt, self.player, self.event_bus, world_rules=world_rules)

        self.camera.update(self.player.x, self.player.y)
        self._check_automatic_extension()

    def _respawn_player(self):
        self.player.x, self.player.y = self.current_area.start_pos
        self.player.alive = True
        self.player.heal_to_full()
        self._respawn_timer = 1.5
        self._show_message("Respawned.")

    def _draw(self):
        self.screen.fill(config.COLOR_BG)

        gates_here = self.world.gates_in_area(self.current_area_id)
        self.current_area.draw(self.screen, self.camera, gates=gates_here)

        for enemy in self.current_area.enemies:
            if enemy.alive:
                enemy.draw(self.screen, self.camera)

        if self.current_area.boss is not None and self.current_area.boss.alive:
            self.current_area.boss.draw(self.screen, self.camera)

        if self.player.alive:
            self.player.draw(self.screen, self.camera)

        boss_for_hud = self.current_area.boss
        self.hud.draw(self.screen, self.player, boss=boss_for_hud, message=self.message)
        self.inventory_ui.draw(self.screen, self.player)
        self._draw_area_label()
        self.evolution_banner.draw(self.screen)
        self.profile_screen.draw(self.screen)
        self.gateway_menu.draw(self.screen)

        pygame.display.flip()

    def _draw_area_label(self):
        font = pygame.font.SysFont("consolas", 18, bold=True)
        if self.current_extension_id is None:
            label = "Starting Region  (TAB: Gateway Portal | F6: Profile | F11: Generate Extension)"
        else:
            ext = self.world.get_extension(self.current_extension_id)
            label = f"{ext.spec.extension_name}  |  Area Lvl {ext.spec.area_level}  (TAB: Gateway Portal)"
        text = font.render(label, True, (220, 220, 230))
        self.screen.blit(text, (config.SCREEN_WIDTH // 2 - text.get_width() // 2, config.SCREEN_HEIGHT - 24))