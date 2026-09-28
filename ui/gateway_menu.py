################################################################################
# FILE: ui\gateway_menu.py  (NEW)
################################################################################

"""
Gateway Portal fast-travel menu. Per spec section 1: "The Starting
Area must feature a permanent Gateway Portal (fast-travel network
hub)." Opened via TAB from anywhere; lists every area reachable
through a currently-OPEN gate (closed gates are not selectable --
they simply don't appear as travel options yet).
"""
import pygame
import core.config as config


class GatewayMenu:
    def __init__(self):
        self.visible = False
        self.entries = []       # list of {"label": str, "target_area_id": str}
        self.selected_index = 0
        self.font_title = pygame.font.SysFont("consolas", 24, bold=True)
        self.font_body = pygame.font.SysFont("consolas", 18)

    def show(self, entries):
        self.entries = entries
        self.selected_index = 0
        self.visible = True

    def hide(self):
        self.visible = False

    def move_selection(self, delta):
        if not self.entries:
            return
        self.selected_index = (self.selected_index + delta) % len(self.entries)

    def confirm_selection(self):
        if not self.entries:
            return None
        return self.entries[self.selected_index]["target_area_id"]

    def draw(self, surface):
        if not self.visible:
            return

        overlay = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        overlay.set_alpha(220)
        overlay.fill((10, 10, 16))
        surface.blit(overlay, (0, 0))

        cx = config.SCREEN_WIDTH // 2
        y = 120
        title = self.font_title.render("GATEWAY PORTAL", True, (120, 220, 255))
        surface.blit(title, (cx - title.get_width() // 2, y))
        y += 50

        hint = self.font_body.render("UP/DOWN: select   ENTER: travel   ESC: cancel", True, (170, 170, 180))
        surface.blit(hint, (cx - hint.get_width() // 2, y))
        y += 50

        for i, entry in enumerate(self.entries):
            color = (255, 230, 120) if i == self.selected_index else (210, 210, 220)
            prefix = "> " if i == self.selected_index else "  "
            line = self.font_body.render(f"{prefix}{entry['label']}", True, color)
            surface.blit(line, (cx - line.get_width() // 2, y))
            y += 30