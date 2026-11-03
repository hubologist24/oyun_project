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
            #surface.blit(text, (20, config.SCREEN_HEIGHT - 44 + i * 18))
            surface.blit(text, (20, config.SCREEN_HEIGHT - 70 + i * 18))


# Focus targets for keyboard navigation
FOCUS_INVENTORY = "inventory"
FOCUS_EQUIPPED = "equipped"
FOCUS_STASH = "stash"


class InventoryUI:
    CELL_SIZE = 34
    CELL_PAD = 3

    BULK_FORGE_ITEMS = 10
    BULK_FORGE_STONES = 3

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
        self.game = None

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

    def do_bulk_forge(self, player) -> str:
        from items.item_generator import generate_random_item
        from items.item import Item

        items = list(player.inventory.items)
        if len(items) < self.BULK_FORGE_ITEMS:
            return (f"Bulk forge needs {self.BULK_FORGE_ITEMS} items "
                    f"({len(items)}/{self.BULK_FORGE_ITEMS}).")
        if player.extension_stones < self.BULK_FORGE_STONES:
            return (f"Bulk forge needs {self.BULK_FORGE_STONES} stones "
                    f"({player.extension_stones}/{self.BULK_FORGE_STONES}).")
        if self.game is None:
            return ""

        to_consume = items[:self.BULK_FORGE_ITEMS]

        avg_ilvl = max(1, sum(it.item_level for it in to_consume) // self.BULK_FORGE_ITEMS)
        rarity_score = 0.0
        for it in to_consume:
            rarity_score += (3 if it.rarity == Item.RARITY_RARE
                            else 2 if it.rarity == Item.RARITY_MAGIC
                            else 1)
        avg_rarity = rarity_score / self.BULK_FORGE_ITEMS

        if avg_rarity >= 2.0:
            p = s = 3           # six-mod
        elif avg_rarity >= 1.4:
            p, s = 3, 2
        else:
            p, s = 2, 2

        target_ilvl = avg_ilvl + 3    # base shrine gives +1..+3, we give a flat +3
        rng = self.game.world.rng_service.get_stream("bulk_forge")
        rules = self.game._active_world_rules()
        new_item = generate_random_item(
            rng, area_level=target_ilvl,
            world_context={"world_rules": rules},
            force_affix_counts=(p, s),
        )
        new_item.display_name_override = f"Forged {new_item.base_name}"

        for it in to_consume:
            player.inventory.remove_item(it)
        player.extension_stones -= self.BULK_FORGE_STONES

        if not player.inventory.add_item(new_item):
            self.game.current_area.loot_drops.append(
                {"x": player.x + 20, "y": player.y, "item": new_item})
            return (f"Forged {new_item.display_name} "
                    f"[{new_item.affix_count_label()}] (dropped to ground)")

        return (f"Forged {new_item.display_name} "
                f"[{new_item.affix_count_label()}] "
                f"from {self.BULK_FORGE_ITEMS} items + {self.BULK_FORGE_STONES} stones")
    

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

            if self._has_multiple_slots(item):
                from items.equipment_slots import ITEM_SLOT_TO_EQUIP_SLOTS
                candidates = ITEM_SLOT_TO_EQUIP_SLOTS[item.slot]
                empty = [s for s in candidates if player.equipped.get(s) is None]
                if empty:
                    player.equip(item, slot=empty[0])
                    return f"Equipped {item.display_name} in {empty[0].replace('_', ' ').title()}"
                
                self._prompt_ring_slot(player, item, candidates)
                return ""  # modal will report via _show_message  

            
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

    def _has_multiple_slots(self, item) -> bool:
        from items.equipment_slots import ITEM_SLOT_TO_EQUIP_SLOTS
        return len(ITEM_SLOT_TO_EQUIP_SLOTS.get(item.slot, [])) > 1

    def _prompt_ring_slot(self, player, item, candidates):
        if self.game is None or self.game.choice_menu is None:
            return
        opts = []
        for slot in candidates:
            cur = player.equipped.get(slot)
            opts.append({
                "id": slot,
                "name": slot.replace("_", " ").title(),
                "desc": f"Replace: {cur.display_name if cur else '(empty)'}",
            })
        self.game.choice_menu.enqueue(
            f"Which slot for {item.display_name}?",
            opts,
            lambda o: self._equip_ring_to_slot(player, item, o["id"]),
            subtitle="Left/Right + Enter or 1/2. ESC cancels.",
            accent=(255, 200, 120),
            cancellable=True,
            immediate=True,          # user-initiated -> no arming delay
        )

    def _equip_ring_to_slot(self, player, item, slot):
        if player.equip(item, slot=slot) and self.game is not None:
            self.game._show_message(
                f"Equipped {item.display_name} in {slot.replace('_', ' ').title()}")    

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
    def draw(self, surface, player,world_rules=None):
        if not self.visible:
            return

        self._hit_regions = []  # rebuilt fresh every frame

        panel_w = 660 if not self.stash_visible else 1130
        panel = pygame.Rect(config.SCREEN_WIDTH // 2 - panel_w // 2, 40, panel_w, 600)
        pygame.draw.rect(surface, (25, 25, 32), panel)
        pygame.draw.rect(surface, (90, 90, 100), panel, 2)

        title = self.font_title.render("Inventory", True, config.COLOR_TEXT)
        surface.blit(title, (panel.x + 10, panel.y + 8))
       
        hint = self.font_small.render(
            "Click/Arrows: select | E/dbl-click: equip | R/right-click: store | "
            "X: forge 10 items -> 1 (3 stones) | B: stash | I: close",
            True, (150, 150, 160))
        
        surface.blit(hint, (panel.x + 10, panel.bottom - 20))

        equipped_x = panel.x + 10
        equipped_y = panel.y + 40
        equipped_w = 230
        self._draw_equipped_panel(surface, player, equipped_x, equipped_y, equipped_w)

        sy = equipped_y + 240
        pts = [(equipped_x + 8, sy), (equipped_x + 16, sy + 9),
            (equipped_x + 8, sy + 18), (equipped_x, sy + 9)]
        pygame.draw.polygon(surface, (170, 90, 255), pts)
        stones = self.font_title.render(f"Extension Stones: {player.extension_stones}",
                                        True, (200, 150, 255))
        surface.blit(stones, (equipped_x + 24, sy))
        sub = self.font_small.render("F11: spend 1 to evolve the world", True, (140, 120, 170))
        surface.blit(sub, (equipped_x + 24, sy + 22))

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

        self._draw_active_tooltip(surface, player, panel,world_rules)

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

    def _draw_active_tooltip(self, surface, player, panel,world_rules=None):
        item = None
        compare_against = None
        compare = True

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
            #compare_against = None
            compare = False


        if item is not None:
            self.tooltip.draw(surface, item, panel.x + 260, panel.y + 300,
                            compare_against=compare_against, compare=compare,
                            world_rules=world_rules, max_width=380)
            
    #def _best_guess_equip_slot(self, item, player):
    #    from items.equipment_slots import resolve_equip_slot
    #    return resolve_equip_slot(item.slot, player.equipped)

    def _best_guess_equip_slot(self, item, player):
        from items.equipment_slots import ITEM_SLOT_TO_EQUIP_SLOTS
        candidates = ITEM_SLOT_TO_EQUIP_SLOTS.get(item.slot, [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        empty = [s for s in candidates if player.equipped.get(s) is None]
        if empty:
            return empty[0]

        def _weight(slot):
            it = player.equipped.get(slot)
            if it is None:
                return float("-inf")
            w = getattr(it, "damage_bonus", 0) + getattr(it, "armor_bonus", 0)
            w += sum(getattr(it, "_other_bonuses", {}).values())
            w += sum(getattr(it, "_resist_bonus", {}).values())
            return w

        return max(candidates, key=_weight)

class DamageNumbers:
    """Floating, fading damage text spawned on hits."""

    def __init__(self):
        self.font = pygame.font.SysFont("consolas", 15, bold=True)
        self.numbers = []  # dicts: x, y, timer, text, color

    def spawn(self, x, y, text, color=(255, 255, 255)):
        self.numbers.append({"x": x, "y": y, "t": 0.8, "text": text, "color": color})

    def update(self, dt):
        for n in self.numbers:
            n["y"] -= 45 * dt
            n["t"] -= dt
        self.numbers = [n for n in self.numbers if n["t"] > 0]

    def draw(self, surface, camera):
        for n in self.numbers:
            alpha = min(255, int(255 * (n["t"] / 0.8)))
            s = self.font.render(n["text"], True, n["color"])
            s.set_alpha(alpha)
            surface.blit(s, camera.world_to_screen((n["x"], n["y"])))    