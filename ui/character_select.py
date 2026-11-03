# [PROJECT: ui]
# [SYMBOLS] classes: CharacterSelect
import time
import pygame
import core.config as config


def _fmt_playtime(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h:02d}h{m:02d}m"


class CharacterSelect:
    """Pre-game character picker.

    Sets `self.result` (slot_id or None for quit) and `self._done` when
    the player confirms. main() then boots Game(slot_id=self.result).
    """

    def __init__(self):
        self.visible = False
        self.entries = []
        self.selected = 0
        self.mode = "select"        # "select" | "name_input" | "confirm_delete"
        self.name_input = ""
        self.pending_delete = None
        self.result = None
        self._done = False

        self.font_title = pygame.font.SysFont("consolas", 30, bold=True)
        self.font_body  = pygame.font.SysFont("consolas", 18)
        self.font_small = pygame.font.SysFont("consolas", 14)

    # ---------------------------------------------------------------- lifecycle
    def open(self):
        from save.save_manager import SaveManager
        self.entries = SaveManager.list_slots()
        self.selected = 0
        self.mode = "select"
        self.name_input = ""
        self.pending_delete = None
        self.result = None
        self._done = False
        self.visible = True

    def is_done(self):
        return self._done

    # ------------------------------------------------------------- input events
    def handle_key(self, key):
        if self.mode == "name_input":
            self._handle_name_input(key)
            return
        if self.mode == "confirm_delete":
            self._handle_confirm_delete(key)
            return
        self._handle_select(key)

    def _handle_select(self, key):
        n = len(self.entries) + 1   # +1 for "Create New Character"
        if key in (pygame.K_UP, pygame.K_w):
            self.selected = (self.selected - 1) % n
        elif key in (pygame.K_DOWN, pygame.K_s):
            self.selected = (self.selected + 1) % n
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            if self.selected == len(self.entries):
                self.mode = "name_input"
                self.name_input = ""
            else:
                self.result = self.entries[self.selected]["slot_id"]
                self._done = True
        elif key == pygame.K_DELETE and self.selected < len(self.entries):
            self.pending_delete = self.entries[self.selected]["slot_id"]
            self.mode = "confirm_delete"
        elif key == pygame.K_ESCAPE:
            self.result = None
            self._done = True

    def _handle_name_input(self, key):
        if key == pygame.K_ESCAPE:
            self.mode = "select"
            return
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if self.name_input.strip():
                from save.save_manager import SaveManager
                self.result = SaveManager.create_slot(self.name_input.strip())
                self._done = True
            return
        if key == pygame.K_BACKSPACE:
            self.name_input = self.name_input[:-1]
            return
        ch = None
        if pygame.K_a <= key <= pygame.K_z:
            ch = chr(key)
            if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                ch = ch.upper()
        elif pygame.K_0 <= key <= pygame.K_9:
            ch = chr(key)
        elif key == pygame.K_SPACE:
            ch = " "
        if ch and len(self.name_input) < 24:
            self.name_input += ch

    def _handle_confirm_delete(self, key):
        if key == pygame.K_y:
            from save.save_manager import SaveManager
            SaveManager.delete_slot(self.pending_delete)
            self.pending_delete = None
            self.open()   # refresh list
        elif key in (pygame.K_n, pygame.K_ESCAPE):
            self.pending_delete = None
            self.mode = "select"

    # ------------------------------------------------------------------- draw
    def draw(self, surface):
        if not self.visible:
            return
        surface.fill((10, 10, 14))
        cx = config.SCREEN_WIDTH // 2

        title = self.font_title.render("SELECT CHARACTER", True, (255, 220, 120))
        surface.blit(title, (cx - title.get_width() // 2, 60))

        if self.mode == "name_input":
            self._draw_name_input(surface, cx)
            return
        if self.mode == "confirm_delete":
            self._draw_confirm_delete(surface, cx)
            return

        y = 160
        if not self.entries:
            msg = self.font_body.render(
                "No characters yet — press ENTER to create one.",
                True, (180, 180, 190))
            surface.blit(msg, (cx - msg.get_width() // 2, y))
            y += 50

        left = cx - 400
        for i, e in enumerate(self.entries):
            selected = (i == self.selected)
            color = (255, 230, 120) if selected else (210, 210, 220)
            prefix = "> " if selected else "  "
            when = time.strftime("%Y-%m-%d %H:%M",
                                 time.localtime(e.get("saved_at", 0))) if e.get("saved_at") else "-"
            line = (f"{prefix}{e['name']:<20}  "
                    f"Lv {e.get('level', 1):<3}  "
                    f"{_fmt_playtime(e.get('playtime', 0.0)):>8}  "
                    f"{when}")
            surface.blit(self.font_body.render(line, True, color), (left, y))
            y += 30

        sel_new = (self.selected == len(self.entries))
        new_color = (255, 230, 120) if sel_new else (140, 255, 140)
        prefix = "> " if sel_new else "  "
        surface.blit(self.font_body.render(
            f"{prefix}+ Create New Character", True, new_color),
            (left, y + 24))

        hint = self.font_small.render(
            "UP/DOWN: select    ENTER: play    DEL: delete    ESC: quit",
            True, (150, 150, 165))
        surface.blit(hint, (cx - hint.get_width() // 2, config.SCREEN_HEIGHT - 60))

    def _draw_name_input(self, surface, cx):
        header = self.font_body.render("Name your adventurer:", True, (220, 220, 230))
        surface.blit(header, (cx - header.get_width() // 2, 220))
        box = pygame.Rect(cx - 200, 270, 400, 56)
        pygame.draw.rect(surface, (30, 30, 40), box, border_radius=4)
        pygame.draw.rect(surface, (120, 220, 255), box, 2, border_radius=4)
        cursor = "|" if (pygame.time.get_ticks() // 500) % 2 == 0 else " "
        surf = self.font_title.render(self.name_input + cursor, True, (255, 255, 255))
        surface.blit(surf, (box.x + 14, box.y + 10))
        hint = self.font_small.render(
            "ENTER: create    ESC: cancel", True, (150, 150, 165))
        surface.blit(hint, (cx - hint.get_width() // 2, box.bottom + 30))

    def _draw_confirm_delete(self, surface, cx):
        entry = next((e for e in self.entries if e["slot_id"] == self.pending_delete), None)
        name = entry["name"] if entry else "?"
        msg = self.font_title.render(f"Delete '{name}'?", True, (255, 100, 100))
        surface.blit(msg, (cx - msg.get_width() // 2, 300))
        hint = self.font_body.render("Y: yes    N / ESC: cancel",
                                     True, (200, 200, 210))
        surface.blit(hint, (cx - hint.get_width() // 2, 380))