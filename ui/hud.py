"""
HUD - Phase 8: InventoryUI reworked for grid-based inventory + full
10-slot equipment panel + stash panel + hover-compare tooltips.

Keyboard-only navigation (no mouse handling exists in this codebase):
  I           - toggle inventory panel
  B           - toggle stash panel
  UP/DOWN/LEFT/RIGHT - move selection cursor within the active grid
  TAB (while inventory or stash open) - switch focus between
                inventory grid / equipped-slots column / stash grid
  E           - equip selected inventory item, OR unequip selected
                equipped-slot item back to inventory, OR (if stash
                focused) move selected stash item into inventory
  R           - move selected inventory item into stash (only when
                stash panel is also open)
"""
import pygame
import core.config as config
from ui.tooltip import ItemTooltip
from items.equipment_slots import EQUIPMENT_SLOTS


class HUD:
    def __init__(self):
        self.font = pygame.font.SysFont("consolas", 16)
        self.font_big = pygame.font.SysFont("consolas", 22, bold=True)

    def draw(self, surface, player, boss=None, message=""):
        self._draw_player_bars(surface, player)
        if boss is not None and boss.alive:
            self._draw_boss_bar(surface, boss)
        if message:
            text = self.font_big.render(message, True, (255, 220, 80))
            surface.blit(text, (config.SCREEN_WIDTH // 2 - text.get_width() // 2, 60))
        self._draw_hints(surface)

    def _draw_player_bars(self, surface, player):
        x, y = 20, 20
        w, h = 240, 18
        stats = player.effective_stats

        pygame.draw.rect(surface, config.COLOR_HP_BAR_BG, (x, y, w, h))
        frac = stats.hp / stats.max_hp if stats.max_hp else 0
        pygame.draw.rect(surface, config.COLOR_HP_BAR_FG, (x, y, int(w * frac), h))
        hp_text = self.font.render(f"HP {stats.hp}/{stats.max_hp}", True, config.COLOR_TEXT)
        surface.blit(hp_text, (x + 6, y - 1))

        y2 = y + h + 6
        xp_needed = player.progression.xp_to_next()
        xp_frac = player.progression.xp / xp_needed if xp_needed else 0
        pygame.draw.rect(surface, (20, 20, 40), (x, y2, w, 10))
        pygame.draw.rect(surface, config.COLOR_XP_BAR_FG, (x, y2, int(w * xp_frac), 10))

        lvl_text = self.font.render(f"Level {player.progression.level}", True, config.COLOR_TEXT)
        surface.blit(lvl_text, (x, y2 + 14))

        dmg_text = self.font.render(
            f"DMG {player.effective_stats.base_damage} ({player.effective_stats.primary_damage_type})"
            f"  ARMOR {player.effective_stats.armor}",
            True, config.COLOR_TEXT)
        surface.blit(dmg_text, (x, y2 + 34))

        if player.effective_stats.resistances:
            res_str = "  ".join(
                f"{k[:4].upper()} {int(v*100)}%" for k, v in player.effective_stats.resistances.items()
                if k != "armor_flat"
            )
            if res_str:
                res_text = self.font.render(res_str, True, (180, 220, 200))
                surface.blit(res_text, (x, y2 + 54))

    def _draw_boss_bar(self, surface, boss):
        w, h = 500, 22
        x = config.SCREEN_WIDTH // 2 - w // 2
        y = 20
        pygame.draw.rect(surface, config.COLOR_HP_BAR_BG, (x, y, w, h))
        frac = boss.hp / boss.max_hp if boss.max_hp else 0
        pygame.draw.rect(surface, config.COLOR_BOSS_BAR_FG, (x, y, int(w * frac), h))
        label = self.font.render(f"{boss.name} - {boss.hp}/{boss.max_hp}", True, config.COLOR_TEXT)
        surface.blit(label, (x + w // 2 - label.get_width() // 2, y + 2))

    def _draw_hints(self, surface):
        hints = [
            "WASD/Arrows: Move   SPACE: Attack   I: Inventory   B: Stash   E: Equip/Move",
            "F1 XP  F2 Item  F3 Rare  F5 Force6Mod  F9 Save  F10 Load  ESC Quit",
        ]
        for i, h in enumerate(hints):
            text = self.font.render(h, True, (170, 170, 180))
            surface.blit(text, (20, config.SCREEN_HEIGHT - 44 + i * 18))


# Focus targets for keyboard navigation
FOCUS_INVENTORY = "inventory"
FOCUS_EQUIPPED = "equipped"
FOCUS_STASH = "stash"


class InventoryUI:
    CELL_SIZE = 34
    CELL_PAD = 3

    def __init__(self):
        self.font = pygame.font.SysFont("consolas", 13)
        self.font_title = pygame.font.SysFont("consolas", 17, bold=True)
        self.font_small = pygame.font.SysFont("consolas", 11)
        self.tooltip = ItemTooltip()

        self.visible = False
        self.stash_visible = False

        self.focus = FOCUS_INVENTORY
        self.inv_cursor = (0, 0)
        self.equipped_index = 0
        self.stash_cursor = (0, 0)

        # Populated fresh each draw() call; used for mouse hit-testing.
        # Maps screen pygame.Rect -> ("inventory"|"stash", (col,row)) or
        # ("equipped", slot_name).
        self._hit_regions = []
        self._last_click_time = 0
        self._last_click_target = None

    # ---------------- mouse ----------------
    def handle_mouse_motion(self, pos, player):
        hit = self._hit_test(pos)
        if hit is None:
            return
        kind, value = hit
        if kind == "inventory":
            self.focus = FOCUS_INVENTORY
            self.inv_cursor = value
        elif kind == "stash":
            self.focus = FOCUS_STASH
            self.stash_cursor = value
        elif kind == "equipped":
            self.focus = FOCUS_EQUIPPED
            self.equipped_index = EQUIPMENT_SLOTS.index(value)

    def handle_mouse_down(self, button, pos, player):
        hit = self._hit_test(pos)
        if hit is None:
            return
        kind, value = hit

        # Move selection to the clicked cell first (mirrors hover behavior).
        if kind == "inventory":
            self.focus = FOCUS_INVENTORY
            self.inv_cursor = value
        elif kind == "stash":
            self.focus = FOCUS_STASH
            self.stash_cursor = value
        elif kind == "equipped":
            self.focus = FOCUS_EQUIPPED
            self.equipped_index = EQUIPMENT_SLOTS.index(value)

        if button == 1:  # left click
            now = pygame.time.get_ticks()
            is_double_click = (
                self._last_click_target == (kind, value) and
                now - self._last_click_time < 350
            )
            self._last_click_time = now
            self._last_click_target = (kind, value)

            if is_double_click:
                # Double-click = perform the context action immediately
                # (equip / unequip / withdraw), same as pressing E.
                self.do_primary_action(player)
        elif button == 3:  # right click = quick-store to stash (inventory only)
            if kind == "inventory" and self.stash_visible:
                self.do_stash_action(player)

    def _hit_test(self, pos):
        for rect, target in self._hit_regions:
            if rect.collidepoint(pos):
                return target
        return None

    # ---------------- toggling ----------------
    def toggle(self):
        self.visible = not self.visible
        if not self.visible:
            self.stash_visible = False

    def toggle_stash(self):
        if not self.visible:
            self.visible = True
        self.stash_visible = not self.stash_visible

    def cycle_focus(self):
        order = [FOCUS_INVENTORY, FOCUS_EQUIPPED] + ([FOCUS_STASH] if self.stash_visible else [])
        idx = order.index(self.focus) if self.focus in order else 0
        self.focus = order[(idx + 1) % len(order)]

    # ---------------- navigation (keyboard) ----------------
    def move_cursor(self, dx, dy, player):
        if self.focus == FOCUS_INVENTORY:
            c, r = self.inv_cursor
            c = max(0, min(player.inventory.cols - 1, c + dx))
            r = max(0, min(player.inventory.rows - 1, r + dy))
            self.inv_cursor = (c, r)
        elif self.focus == FOCUS_STASH:
            c, r = self.stash_cursor
            c = max(0, min(player.stash.cols - 1, c + dx))
            r = max(0, min(player.stash.rows - 1, r + dy))
            self.stash_cursor = (c, r)
        elif self.focus == FOCUS_EQUIPPED:
            step = dy if dy != 0 else dx
            self.equipped_index = max(0, min(len(EQUIPMENT_SLOTS) - 1, self.equipped_index + step))

    # ---------------- current selection helpers ----------------
    def selected_inventory_item(self, player):
        return player.inventory.get_at(self.inv_cursor)

    def selected_stash_item(self, player):
        return player.stash.get_at(self.stash_cursor)

    def selected_equipped_slot(self):
        return EQUIPMENT_SLOTS[self.equipped_index]

    def selected_equipped_item(self, player):
        return player.equipped.get(self.selected_equipped_slot())

    # ---------------- action: E key / double-click ----------------
    def do_primary_action(self, player) -> str:
        if self.focus == FOCUS_INVENTORY:
            item = self.selected_inventory_item(player)
            if item is None:
                return ""
            ok, missing = player.meets_requirements(item)
            if not ok:
                return f"Requires {', '.join(missing)}"
            player.equip(item)
            return f"Equipped {item.display_name}"

        if self.focus == FOCUS_EQUIPPED:
            slot = self.selected_equipped_slot()
            item = player.equipped.get(slot)
            if item is None:
                return ""
            if player.inventory.is_full():
                return "Inventory full -- cannot unequip."
            player.equipped[slot] = None
            player.inventory.add_item(item)
            player.recalc_stats()
            return f"Unequipped {item.display_name}"

        if self.focus == FOCUS_STASH:
            item = self.selected_stash_item(player)
            if item is None:
                return ""
            if player.inventory.is_full():
                return "Inventory full -- cannot withdraw."
            player.stash.remove_item(item)
            player.inventory.add_item(item)
            return f"Moved {item.display_name} to inventory"

        return ""

    # ---------------- action: R key / right-click ----------------
    def do_stash_action(self, player) -> str:
        if self.focus != FOCUS_INVENTORY or not self.stash_visible:
            return ""
        item = self.selected_inventory_item(player)
        if item is None:
            return ""
        if player.stash.is_full():
            return "Stash full -- cannot store."
        player.inventory.remove_item(item)
        player.stash.add_item(item)
        return f"Stored {item.display_name}"

    # ---------------- drawing ----------------
    def draw(self, surface, player):
        if not self.visible:
            return

        self._hit_regions = []  # rebuilt fresh every frame

        panel_w = 460 if not self.stash_visible else 900
        panel = pygame.Rect(config.SCREEN_WIDTH // 2 - panel_w // 2, 40, panel_w, 620)
        pygame.draw.rect(surface, (25, 25, 32), panel)
        pygame.draw.rect(surface, (90, 90, 100), panel, 2)

        title = self.font_title.render(
            "Inventory  (Click/Arrows: select | E/dbl-click: equip | R/right-click: store | "
            "B: stash | I: close)", True, config.COLOR_TEXT)
        surface.blit(title, (panel.x + 10, panel.y + 8))

        equipped_x = panel.x + 10
        equipped_y = panel.y + 40
        equipped_w = 230
        self._draw_equipped_panel(surface, player, equipped_x, equipped_y, equipped_w)

        inv_x = equipped_x + equipped_w + 20
        inv_y = equipped_y
        self._draw_grid_panel(surface, player.inventory, self.inv_cursor,
                               focused=(self.focus == FOCUS_INVENTORY),
                               x=inv_x, y=inv_y, label="Inventory", region_kind="inventory")

        if self.stash_visible:
            stash_x = inv_x + (player.inventory.cols * (self.CELL_SIZE + self.CELL_PAD)) + 24
            self._draw_grid_panel(surface, player.stash, self.stash_cursor,
                                   focused=(self.focus == FOCUS_STASH),
                                   x=stash_x, y=inv_y, label="Stash", region_kind="stash")

        self._draw_active_tooltip(surface, player, panel)

    def _draw_equipped_panel(self, surface, player, x, y, width):
        header = self.font.render("Equipped", True, (200, 230, 255))
        surface.blit(header, (x, y))
        y += 22
        for i, slot in enumerate(EQUIPMENT_SLOTS):
            item = player.equipped.get(slot)
            row_rect = pygame.Rect(x, y, width, 20)
            if self.focus == FOCUS_EQUIPPED and i == self.equipped_index:
                pygame.draw.rect(surface, (55, 55, 70), row_rect)
            label_color = item.rarity_color() if item else (110, 110, 120)
            name = item.display_name if item else "-"
            text = self.font.render(f"{slot.replace('_', ' ').title():<9}: {name}", True, label_color)
            surface.blit(text, (x + 4, y + 2))
            self._hit_regions.append((row_rect, ("equipped", slot)))
            y += 20

    def _draw_grid_panel(self, surface, grid_obj, cursor, focused, x, y, label, region_kind):
        header_color = (255, 240, 190) if focused else (180, 180, 190)
        header = self.font.render(f"{label} ({len(grid_obj.items)}/{grid_obj.cols * grid_obj.rows})",
                                   True, header_color)
        surface.blit(header, (x, y))
        y += 20

        cs, pad = self.CELL_SIZE, self.CELL_PAD
        for r in range(grid_obj.rows):
            for c in range(grid_obj.cols):
                cell_rect = pygame.Rect(x + c * (cs + pad), y + r * (cs + pad), cs, cs)
                item = grid_obj.get_at((c, r))
                bg = (45, 45, 55) if item else (32, 32, 40)
                pygame.draw.rect(surface, bg, cell_rect)
                is_selected = focused and cursor == (c, r)
                border_color = (255, 220, 90) if is_selected else (70, 70, 80)
                pygame.draw.rect(surface, border_color, cell_rect, 2 if is_selected else 1)
                if item:
                    pygame.draw.circle(surface, item.rarity_color(), cell_rect.center, cs // 2 - 6)
                self._hit_regions.append((cell_rect, (region_kind, (c, r))))

    def _draw_active_tooltip(self, surface, player, panel):
        item = None
        compare_against = None

        if self.focus == FOCUS_INVENTORY:
            item = self.selected_inventory_item(player)
            if item is not None:
                compare_against = player.equipped.get(self._best_guess_equip_slot(item, player))
        elif self.focus == FOCUS_STASH:
            item = self.selected_stash_item(player)
            if item is not None:
                compare_against = player.equipped.get(self._best_guess_equip_slot(item, player))
        elif self.focus == FOCUS_EQUIPPED:
            item = self.selected_equipped_item(player)
            compare_against = None

        if item is not None:
            self.tooltip.draw(surface, item, panel.right + 12, panel.y, compare_against=compare_against)

    def _best_guess_equip_slot(self, item, player):
        from items.equipment_slots import resolve_equip_slot
        return resolve_equip_slot(item.slot, player.equipped)