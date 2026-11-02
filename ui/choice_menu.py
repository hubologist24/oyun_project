"""Generic modal 'pick 1 of N' card menu (perks, keystones, push-back reason)."""
import textwrap
import pygame
import core.config as config


class ChoiceMenu:
    CARD_W, CARD_H, GAP = 300, 230, 24

    def __init__(self):
        self.visible = False
        self.selected = 0
        self._queue = []
        self._cur = None
        self._dim = None
        self._f_title = pygame.font.SysFont("consolas", 30, bold=True)
        self._f_sub = pygame.font.SysFont("consolas", 16)
        self._f_name = pygame.font.SysFont("consolas", 20, bold=True)
        self._f_body = pygame.font.SysFont("consolas", 15)

    # ---- queue ----
    def enqueue(self, title, options, on_pick, subtitle="", accent=(120, 220, 255),
                cancellable=False):
        self._queue.append(dict(title=title, options=options, on_pick=on_pick,
                                subtitle=subtitle, accent=accent, cancellable=cancellable))
        if not self.visible:
            self._next()

    def _next(self):
        if not self._queue:
            self.visible = False
            self._cur = None
            return
        self._cur = self._queue.pop(0)
        self.selected = 0
        self.visible = True

    def _pick(self, idx):
        cur = self._cur
        if cur is None or not (0 <= idx < len(cur["options"])):
            return
        opt = cur["options"][idx]
        self._next()                 # advance first so the callback may enqueue
        cur["on_pick"](opt)

    # ---- layout ----
    def _rects(self):
        n = len(self._cur["options"])
        total = n * self.CARD_W + (n - 1) * self.GAP
        x0 = (config.SCREEN_WIDTH - total) // 2
        y = (config.SCREEN_HEIGHT - self.CARD_H) // 2 + 20
        return [pygame.Rect(x0 + i * (self.CARD_W + self.GAP), y, self.CARD_W, self.CARD_H)
                for i in range(n)]

    # ---- input ----
    def handle_key(self, key):
        if not self.visible or self._cur is None:
            return False
        n = len(self._cur["options"])
        if pygame.K_1 <= key <= pygame.K_9:
            self._pick(key - pygame.K_1)
        elif key == pygame.K_LEFT:
            self.selected = (self.selected - 1) % n
        elif key == pygame.K_RIGHT:
            self.selected = (self.selected + 1) % n
        elif key in (pygame.K_RETURN, pygame.K_SPACE):
            self._pick(self.selected)
        elif key == pygame.K_ESCAPE and self._cur["cancellable"]:
            self._next()
        return True

    def handle_click(self, button, pos):
        if not self.visible or self._cur is None or button != 1:
            return
        for i, r in enumerate(self._rects()):
            if r.collidepoint(pos):
                self._pick(i)
                return

    def handle_motion(self, pos):
        if not self.visible or self._cur is None:
            return
        for i, r in enumerate(self._rects()):
            if r.collidepoint(pos):
                self.selected = i

    # ---- draw ----
    def draw(self, surface):
        if not self.visible or self._cur is None:
            return
        if self._dim is None:
            self._dim = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT), pygame.SRCALPHA)
            self._dim.fill((0, 0, 0, 175))
        surface.blit(self._dim, (0, 0))
        cur = self._cur
        accent = cur["accent"]

        t = self._f_title.render(cur["title"], True, accent)
        surface.blit(t, (config.SCREEN_WIDTH // 2 - t.get_width() // 2, 110))
        if cur["subtitle"]:
            s = self._f_sub.render(cur["subtitle"], True, (200, 200, 210))
            surface.blit(s, (config.SCREEN_WIDTH // 2 - s.get_width() // 2, 156))

        for i, (r, opt) in enumerate(zip(self._rects(), cur["options"])):
            sel = (i == self.selected)
            pygame.draw.rect(surface, (30, 30, 44) if sel else (22, 22, 32), r, border_radius=10)
            pygame.draw.rect(surface, accent if sel else (80, 80, 95), r, 3 if sel else 1,
                             border_radius=10)
            num = self._f_sub.render(f"[{i + 1}]", True, (160, 160, 175))
            surface.blit(num, (r.x + 14, r.y + 12))
            name = self._f_name.render(opt["name"], True, accent if sel else (235, 235, 235))
            surface.blit(name, (r.x + 14, r.y + 44))
            y = r.y + 86
            for line in textwrap.wrap(opt["desc"], 30):
                surface.blit(self._f_body.render(line, True, (205, 205, 215)), (r.x + 14, y))
                y += 20

        hint = "1-3 / click to choose" + ("   |   ESC to skip" if cur["cancellable"] else "")
        h = self._f_sub.render(hint, True, (150, 150, 165))
        surface.blit(h, (config.SCREEN_WIDTH // 2 - h.get_width() // 2, config.SCREEN_HEIGHT - 110))