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
        self.font_title = pygame.font.SysFont("consolas", 26, bold=True)
        self.font_body = pygame.font.SysFont("consolas", 18)

    def show(self, profile):
        self.profile = profile
        self.visible = True

    def hide(self):
        self.visible = False

    def draw(self, surface):
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
            f"Average Combat Distance: {d['average_combat_distance']} px",
            "",
            f"Playtime: {d['playtime_seconds']:.0f}s    Extensions Generated: {d['extensions_generated']}",
        ]
        for line in lines:
            text = self.font_body.render(line, True, (220, 220, 225))
            surface.blit(text, (x, y))
            y += 26