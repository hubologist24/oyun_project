"""Input handling: events, mouse, keyboard."""
import pygame

import core.config as config
from core.difficulty import D


class InputMixin:

    def _handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                try:
                    self._save_game()
                except Exception as e:
                    print(f"[Game] Auto-save on quit failed: {e}")
                self.running = False
            elif event.type == pygame.KEYDOWN:
                self._handle_keydown(event.key)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                self._handle_mouse_down(event.button, event.pos)
            elif event.type == pygame.MOUSEBUTTONUP:
                self._handle_mouse_up(event.button, event.pos)
            elif event.type == pygame.MOUSEWHEEL:
                if self.profile_screen.visible:
                    self.profile_screen.scroll_by(-event.y * 3)
            elif event.type == pygame.MOUSEMOTION:
                self._handle_mouse_motion(event.pos)

    def _handle_mouse_down(self, button, pos):
        if self.choice_menu.visible:
            self.choice_menu.handle_click(button, pos)
            return

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
        if self.choice_menu.visible:
            self.choice_menu.handle_motion(pos)
            return

        if self.inventory_ui.visible:
            self.inventory_ui.handle_mouse_motion(pos, self.player)
            return

        if not self._move_button_held or not self.player.alive:
            return
        if (self.evolution_banner.visible or self.profile_screen.visible
                or self.gateway_menu.visible or self.inventory_ui.visible
                or self.choice_menu.visible):
            return

        world_pos = self.camera.screen_to_world(pos)
        self.player.set_move_target(*world_pos)

    def _handle_mouse_up(self, button, pos):
        if button == 1:
            self._move_button_held = False
            if self.player.alive:
                self.player.clear_move_target()
        elif button == 3:
            self._attack_button_held = False

    def _handle_keydown(self, key):
        mods = pygame.key.get_mods()
        shift_held = mods & pygame.KMOD_SHIFT

        # --- live difficulty tuning (works everywhere) ---
        if key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            D.adjust(-5)
            self._show_message(f"Difficulty: {D.label()}")
            return
        if key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
            D.adjust(+5)
            self._show_message(f"Difficulty: {D.label()}")
            return

        if self.choice_menu.visible:
            self.choice_menu.handle_key(key)
            return

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
            elif key == pygame.K_LSHIFT:
                self.player.face_toward(*self.camera.screen_to_world(pygame.mouse.get_pos()))
                self.player.try_dash()
            return

        if self.profile_screen.visible:
            if key in (pygame.K_F6, pygame.K_ESCAPE):
                self.profile_screen.hide()
            elif key == pygame.K_TAB:
                self.profile_screen.toggle_view()
            elif key == pygame.K_DOWN:
                self.profile_screen.scroll_by(3)
            elif key == pygame.K_UP:
                self.profile_screen.scroll_by(-3)
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
            if key == pygame.K_x:
                msg = self.inventory_ui.do_bulk_forge(self.player)
                if msg:
                    self._show_message(msg, duration=3.5)
                return
            if key == pygame.K_r:
                msg = self.inventory_ui.do_stash_action(self.player)
                if msg:
                    self._show_message(msg)
                return
            return

        if key == pygame.K_ESCAPE:
            try:
                self._save_game()
            except Exception as e:
                print(f"[Game] Auto-save on exit failed: {e}")
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
        elif key == pygame.K_F4:
            self.show_region_details = not self.show_region_details
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