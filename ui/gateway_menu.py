# [PROJECT: ui]
import pygame
import core.config as config


class GatewayMenu:
    def __init__(self):
        if not pygame.font.get_init():
            pygame.font.init()
        self.visible = False
        self.entries = []
        self.selected_index = 0
        self.mode = "travel"            # "travel" | "generate"
        self.title = "GATEWAY PORTAL"
        self.subtitle = "UP/DOWN: select   ENTER: travel   ESC: cancel"
        self.cancellable = False
        self.font_title = pygame.font.SysFont("consolas", 24, bold=True)
        self.font_body = pygame.font.SysFont("consolas", 18)
        self.font_small = pygame.font.SysFont("consolas", 14)

    def show(self, entries, mode="travel", title=None, subtitle=None,
             cancellable=False):
        self.entries = entries
        self.selected_index = 0
        self.visible = True
        self.mode = mode
        if title is not None:
            self.title = title
        else:
            self.title = ("GATEWAY PORTAL" if mode == "travel"
                          else "CHOOSE YOUR DESTINATION")
        if subtitle is not None:
            self.subtitle = subtitle
        else:
            self.subtitle = ("UP/DOWN: select   ENTER: confirm   ESC: cancel"
                             if cancellable
                             else "UP/DOWN: select   ENTER: travel")
        self.cancellable = cancellable

    def hide(self):
        self.visible = False
        self.entries = []
        self.selected_index = 0
        self.mode = "travel"
        self.cancellable = False

    def move_selection(self, delta):
        if not self.entries:
            return
        self.selected_index = (self.selected_index + delta) % len(self.entries)

    def confirm_selection(self):
        if not self.visible or not self.entries:
            return None
        entry = self.entries[self.selected_index]
        if self.mode == "generate":
            return entry.get("gate")
        return entry.get("target_area_id")

    # ------------------------------------------------------------------
    def draw(self, surface):
        if not self.visible:
            return

        overlay = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        overlay.set_alpha(220)
        overlay.fill((10, 10, 16))
        surface.blit(overlay, (0, 0))

        cx = config.SCREEN_WIDTH // 2
        y = 120

        title = self.font_title.render(self.title, True, (120, 220, 255))
        surface.blit(title, (cx - title.get_width() // 2, y))
        y += 44

        hint = self.font_body.render(self.subtitle, True, (170, 170, 180))
        surface.blit(hint, (cx - hint.get_width() // 2, y))
        y += 44

        if self.mode == "generate":
            self._draw_gate_choices(surface, cx, y)
        else:
            self._draw_travel_choices(surface, cx, y)

    def _draw_travel_choices(self, surface, cx, y):
        for i, entry in enumerate(self.entries):
            color = (255, 230, 120) if i == self.selected_index else (210, 210, 220)
            prefix = "> " if i == self.selected_index else "  "
            line = self.font_body.render(f"{prefix}{entry['label']}", True, color)
            surface.blit(line, (cx - line.get_width() // 2, y))
            y += 30

    def _draw_gate_choices(self, surface, cx, y):
        from world.gate_tiers import GATE_TIERS

        for i, entry in enumerate(self.entries):
            selected = (i == self.selected_index)
            tier_id = entry.get("tier", "standard")
            tier = GATE_TIERS.get(tier_id, GATE_TIERS["standard"])
            tier_color = tier["color"]

            badge = self.font_small.render(f"[{tier['label']}]", True, tier_color)
            label_color = (255, 230, 120) if selected else (220, 220, 230)
            prefix = "> " if selected else "  "
            label = self.font_body.render(
                f"{prefix}{entry['label']}", True, label_color)

            line_y = y
            surface.blit(badge, (cx - 420, line_y + 2))
            surface.blit(label, (cx - 420 + badge.get_width() + 10, line_y))

            desc = self.font_small.render(
                f"    {entry.get('description', tier['description'])}",
                True, (170, 170, 185))
            surface.blit(desc, (cx - 420, line_y + 22))

            seal_desc = entry.get("seal_desc", "")
            if seal_desc:
                seal = self.font_small.render(
                    f"    {seal_desc}", True, (140, 140, 155))
                surface.blit(seal, (cx - 420, line_y + 40))

            y += 66