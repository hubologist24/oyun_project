"""
World Evolution banner: shown when a new extension is generated,
matching the spec's mock-up. Pauses gameplay update loop while visible
(handled in core/game.py) and dismisses on SPACE/ENTER/ESC.
"""
import pygame
import core.config as config


class WorldEvolutionBanner:
    def __init__(self):
        self.visible = False
        self.extension_name = ""
        self.area_level = 0
        self.is_anomaly = False
        self.rule_names = []
        self.font_title = pygame.font.SysFont("consolas", 30, bold=True)
        self.font_body = pygame.font.SysFont("consolas", 20)
        self.font_small = pygame.font.SysFont("consolas", 16)

    def show(self, extension_name, area_level, is_anomaly, rule_names):
        self.visible = True
        self.extension_name = extension_name
        self.area_level = area_level
        self.is_anomaly = is_anomaly
        self.rule_names = rule_names

    def dismiss(self):
        self.visible = False

    def draw(self, surface):
        if not self.visible:
            return

        overlay = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        overlay.set_alpha(230)
        overlay.fill((8, 8, 12))
        surface.blit(overlay, (0, 0))

        cx = config.SCREEN_WIDTH // 2
        y = 140

        header = self.font_title.render("WORLD EVOLUTION", True, (255, 215, 90))
        surface.blit(header, (cx - header.get_width() // 2, y))
        y += 60

        sub = self.font_body.render("Your journey has changed the world.", True, (200, 200, 210))
        surface.blit(sub, (cx - sub.get_width() // 2, y))
        y += 50

        tag = "ANOMALY REGION DETECTED" if self.is_anomaly else "NEW REGION DISCOVERED"
        tag_color = (255, 90, 90) if self.is_anomaly else (120, 220, 255)
        tag_text = self.font_body.render(tag, True, tag_color)
        surface.blit(tag_text, (cx - tag_text.get_width() // 2, y))
        y += 40

        name_text = self.font_title.render(self.extension_name.upper(), True, (255, 255, 255))
        surface.blit(name_text, (cx - name_text.get_width() // 2, y))
        y += 50

        lvl_text = self.font_body.render(f"Area Level: {self.area_level}", True, (220, 220, 220))
        surface.blit(lvl_text, (cx - lvl_text.get_width() // 2, y))
        y += 40

        if self.rule_names:
            rule_header = self.font_body.render("New World Rules:", True, (180, 255, 180))
            surface.blit(rule_header, (cx - rule_header.get_width() // 2, y))
            y += 30
            for name in self.rule_names:
                line = self.font_small.render(f"- {name}", True, (180, 255, 180))
                surface.blit(line, (cx - line.get_width() // 2, y))
                y += 24

        y += 30
        hint = self.font_small.render("Press SPACE / ENTER to continue", True, (150, 150, 160))
        surface.blit(hint, (cx - hint.get_width() // 2, y))