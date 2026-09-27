"""
HUD - Phase 2: inventory panel now shows tier, item level, prefix/suffix
list and affix count label (per spec: 'inventory should clearly show
item name, rarity, base type, item level, prefix list, suffix list,
tier, damage, special modifiers').
"""
import pygame
import core.config as config


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
            "WASD/Arrows: Move   SPACE: Attack   I: Inventory   E: Equip best",
            "F1 XP  F2 Item  F3 Rare  F5 Force6Mod  F9 Save  F10 Load  ESC Quit",
        ]
        for i, h in enumerate(hints):
            text = self.font.render(h, True, (170, 170, 180))
            surface.blit(text, (20, config.SCREEN_HEIGHT - 44 + i * 18))


class InventoryUI:
    def __init__(self):
        self.font = pygame.font.SysFont("consolas", 14)
        self.font_title = pygame.font.SysFont("consolas", 18, bold=True)
        self.visible = False
        self.selected_index = 0

    def toggle(self):
        self.visible = not self.visible

    def draw(self, surface, player):
        if not self.visible:
            return
        panel = pygame.Rect(config.SCREEN_WIDTH - 420, 20, 400, 660)
        pygame.draw.rect(surface, (25, 25, 32), panel)
        pygame.draw.rect(surface, (90, 90, 100), panel, 2)

        title = self.font_title.render("Inventory  (UP/DOWN select, E equip)", True, config.COLOR_TEXT)
        surface.blit(title, (panel.x + 10, panel.y + 8))

        y = panel.y + 34
        eq = player.equipped
        for slot_name in ("weapon", "armor"):
            item = eq.get(slot_name)
            label = f"{slot_name.upper()}: {item.display_name if item else '-'}"
            eq_line = self.font.render(label, True, (200, 230, 255))
            surface.blit(eq_line, (panel.x + 10, y))
            y += 18
            if item:
                self._draw_item_affixes(surface, item, panel.x + 20, y)
                y += 16 * (len(item.all_affixes()) + 1)
        y += 10
        pygame.draw.line(surface, (80, 80, 90), (panel.x + 10, y), (panel.right - 10, y))
        y += 10

        for idx, item in enumerate(player.inventory.items):
            color = item.rarity_color()
            highlight = (45, 45, 55) if idx == self.selected_index else None
            name_line = f"{item.display_name}  [{item.affix_count_label()}]  ilvl {item.item_level}"
            if highlight:
                pygame.draw.rect(surface, highlight, (panel.x + 4, y - 2, panel.width - 8, 16))
            line = self.font.render(name_line, True, color)
            surface.blit(line, (panel.x + 10, y))
            y += 16
            if y > panel.bottom - 20:
                break

    def _draw_item_affixes(self, surface, item, x, y):
        if item.implicit:
            line = self.font.render(f"  (implicit) {item.implicit.format_line()}", True, (160, 160, 170))
            surface.blit(line, (x, y))
            y += 16
        for p in item.prefixes:
            line = self.font.render(f"  {p.display_name}: {p.format_line()}", True, (150, 190, 255))
            surface.blit(line, (x, y))
            y += 16
        for s in item.suffixes:
            line = self.font.render(f"  {s.display_name}: {s.format_line()}", True, (170, 255, 190))
            surface.blit(line, (x, y))
            y += 16