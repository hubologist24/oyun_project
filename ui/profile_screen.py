"""
Debug/dev screen: F6 = Show Player Profile (per spec's debug key list).
Displays the aggregated PlayerProfile in a readable panel. This is
exactly the payload that will later be serialized and handed to
Phase 5's ProceduralWorldGenerator / Phase 6's LLMWorldGenerator.
"""
import pygame
import core.config as config


class ProfileScreen:
    def __init__(self):
        self.visible = False
        self.profile = None

        self.codex = None
        self.view_mode = "profile"    # "profile" | "codex"
        self.font_title = pygame.font.SysFont("consolas", 26, bold=True)
        self.font_body = pygame.font.SysFont("consolas", 18)
        self.font_small = pygame.font.SysFont("consolas", 14)

    def show(self, profile, codex=None):
        self.profile = profile
        if codex is not None:
            self.codex = codex
        self.visible = True

    def hide(self):
        self.visible = False

    def toggle_view(self):
        self.view_mode = "codex" if self.view_mode == "profile" else "profile"

    def draw(self, surface):
        if not self.visible or self.profile is None:
            return
        overlay = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        overlay.set_alpha(235)
        overlay.fill((10, 10, 14))
        surface.blit(overlay, (0, 0))

        if self.view_mode == "codex" and self.codex is not None:
            self._draw_codex(surface)
        else:
            self._draw_profile(surface)

    def _draw_profile(self, surface):
        # (Existing content, only the title line changes:)
        x, y = 80, 50
        title = self.font_title.render(
            "PLAYER PROFILE  (TAB: Codex | F6/ESC: close)",
            True, (255, 220, 120))
        surface.blit(title, (x, y))
        y += 50
        # ... rest unchanged from original draw() ...

    def _draw_codex(self, surface):
        from history.codex import ACHIEVEMENTS
        x, y = 80, 50
        title = self.font_title.render(
            "THE LIVING CODEX  (TAB: Profile | F6/ESC: close)",
            True, (180, 220, 255))
        surface.blit(title, (x, y))
        y += 50

        unlocked_count = len(self.codex.unlocked)
        total = len(ACHIEVEMENTS)
        head = self.font_body.render(
            f"Legacy Bonuses Unlocked: {unlocked_count} / {total}",
            True, (200, 200, 220))
        surface.blit(head, (x, y))
        y += 40

        for aid, ach in ACHIEVEMENTS.items():
            unlocked = aid in self.codex.unlocked
            current, target = self.codex.progress_for(aid)
            color = (240, 220, 120) if unlocked else (150, 150, 160)
            marker = "[✓]" if unlocked else "[ ]"
            line = f"{marker} {ach['name']}"
            surf = self.font_body.render(line, True, color)
            surface.blit(surf, (x, y))
            y += 22
            desc = self.font_small.render("    " + ach["description"],
                                          True, (170, 170, 180))
            surface.blit(desc, (x, y))
            y += 20
            if not unlocked and ach.get("target"):
                prog = self.font_small.render(
                    f"    Progress: {current}/{target}",
                    True, (140, 140, 160))
                surface.blit(prog, (x, y))
                y += 18
            if unlocked:
                bonus_str = ", ".join(f"+{v} {k}" for k, v in ach["bonus"].items())
                bsurf = self.font_small.render(f"    Bonus: {bonus_str}",
                                               True, (110, 230, 110))
                surface.blit(bsurf, (x, y))
                y += 18
            y += 6
            if y > config.SCREEN_HEIGHT - 60:
                more = self.font_small.render(
                    "...(remaining achievements scroll off; all data is saved)",
                    True, (140, 140, 160))
                surface.blit(more, (x, y))
                break

    def draw_sil(self, surface):
        if not self.visible or self.profile is None:
            return

        overlay = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        overlay.set_alpha(235)
        overlay.fill((10, 10, 14))
        surface.blit(overlay, (0, 0))

        x = 80
        y = 50
        title = self.font_title.render("PLAYER PROFILE  (F6 / ESC to close)", True, (255, 220, 120))
        surface.blit(title, (x, y))
        y += 50

        d = self.profile.to_dict()
        lines = [
            f"Level: {d['level']}",
            f"Main Damage: {d['main_damage']}    Secondary: {d['secondary_damage']}",
            f"Favorite Skill: {d['favorite_skill']}    Most Used Weapon: {d['most_used_weapon']}",
            f"Defensive Strength: {d['defensive_strength']}",
            "",
            f"Deaths (by cause): {d['deaths']}    Total: {d['total_deaths']}",
            f"Boss Attempts: {d['boss_attempts']}    Boss Successes: {d['boss_successes']}",
            "",
            f"Exploration (current area): {d['exploration']*100:.1f}%",
            f"Exploration (average across areas): {d['average_exploration']*100:.1f}%",
            "",
            f"Rare Items Found: {d['rare_items_found']}    Unique Items Found: {d['unique_items_found']}",
            f"Six-Mod (3P/3S) Items Found: {d['six_mod_items_found']}",
            f"Items Kept: {d['items_kept']}    Items Discarded: {d['items_discarded']}",
            "",
            f"Melee/Ranged Preference: {d['melee_ranged_preference']}",
            f"Nemesis Trophies: {', '.join(d['trophies']) or 'none'}",
            f"Average Combat Distance: {d['average_combat_distance']} px",
            "",
            f"Playtime: {d['playtime_seconds']:.0f}s    Extensions Generated: {d['extensions_generated']}",
            f"Favorite Technique: {d['favorite_technique']}",
        ]
        for line in lines:
            text = self.font_body.render(line, True, (220, 220, 225))
            surface.blit(text, (x, y))
            y += 26