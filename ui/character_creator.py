"""
Character Creator UI (dev/tuning tool).

Press `C` in-game to open. Type a level (1-200), press ENTER.
The current character is reset to that level with freshly-rolled
gear equipped in every slot.
"""
import pygame
import core.config as config


class CharacterCreatorUI:
    MIN_LEVEL = 1
    MAX_LEVEL = 200

    def __init__(self):
        self.visible = False
        self.level_text = "1"
        self._font = pygame.font.SysFont("consolas", 32)
        self._font_big = pygame.font.SysFont("consolas", 26, bold=True)
        self._font_small = pygame.font.SysFont("consolas", 16)

    def show(self):
        self.visible = True
        self.level_text = "1"

    def hide(self):
        self.visible = False

    # ------------------------- input -------------------------
    def handle_key(self, key):
        """
        Returns:
            None  -> still editing, or cancelled
            int   -> confirmed level (UI is already hidden)
        """
        if not self.visible:
            return None

        if key == pygame.K_ESCAPE:
            self.hide()
            return None

        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            return self._confirm()

        if key == pygame.K_BACKSPACE:
            self.level_text = self.level_text[:-1] or "0"
            return None

        digit = None
        if pygame.K_0 <= key <= pygame.K_9:
            digit = chr(key)
        elif pygame.K_KP0 <= key <= pygame.K_KP9:
            digit = chr(key - pygame.K_KP0 + ord("0"))

        if digit is not None:
            stripped = self.level_text.lstrip("0")
            if len(stripped) < len(str(self.MAX_LEVEL)):
                new = stripped + digit
                self.level_text = new if new else "0"
        return None

    def _confirm(self):
        try:
            lvl = int(self.level_text)
        except ValueError:
            lvl = self.MIN_LEVEL
        lvl = max(self.MIN_LEVEL, min(self.MAX_LEVEL, lvl))
        self.hide()
        return lvl

    # ------------------------- draw -------------------------
    def draw(self, surface):
        if not self.visible:
            return

        overlay = pygame.Surface(
            (config.SCREEN_WIDTH, config.SCREEN_HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 205))
        surface.blit(overlay, (0, 0))

        cx = config.SCREEN_WIDTH // 2
        cy = config.SCREEN_HEIGHT // 2

        title = self._font_big.render("CHARACTER CREATOR", True, (255, 220, 120))
        surface.blit(title, (cx - title.get_width() // 2, cy - 160))

        subtitle = self._font_small.render(
            "Enter a level. Every equipment slot gets a fresh item at that level.",
            True, (200, 200, 210))
        surface.blit(subtitle, (cx - subtitle.get_width() // 2, cy - 110))

        label = self._font_small.render(
            f"Level ({self.MIN_LEVEL}\u2013{self.MAX_LEVEL}):",
            True, (220, 220, 230))
        surface.blit(label, (cx - 200, cy - 40))

        box = pygame.Rect(cx - 200, cy - 10, 400, 64)
        pygame.draw.rect(surface, (30, 30, 40), box, border_radius=4)
        pygame.draw.rect(surface, (120, 220, 255), box, 2, border_radius=4)

        cursor_on = (pygame.time.get_ticks() // 500) % 2 == 0
        display = self.level_text + ("|" if cursor_on else " ")
        text = self._font.render(display, True, (255, 255, 255))
        surface.blit(text, (box.x + 16, box.y + 12))

        hint = self._font_small.render(
            "ENTER: create    ESC: cancel    BACKSPACE: edit",
            True, (150, 150, 165))
        surface.blit(hint, (cx - hint.get_width() // 2, cy + 80))