import math
import pygame
import core.config as config

_font = None


def draw_objective_pointer(surface, camera, px, py, tx, ty, label, color=(255, 215, 90)):
    global _font
    if _font is None:
        _font = pygame.font.SysFont("consolas", 16, bold=True)

    W, H, M = config.SCREEN_WIDTH, config.SCREEN_HEIGHT, 56
    dx, dy = tx - px, ty - py
    dist = math.hypot(dx, dy)
    if dist < 1:
        return
    ux, uy = dx / dist, dy / dist

    sr = camera.world_to_screen_rect(pygame.Rect(int(tx) - 1, int(ty) - 1, 2, 2))
    sx, sy = sr.centerx, sr.centery
    on_screen = M <= sx <= W - M and M <= sy <= H - M

    if on_screen:
        pos, (ax, ay) = (sx, sy - 40), (0, 1)          # bobbing arrow above target
        ax, ay = 0, 1
    else:
        sc = min((W / 2 - M) / abs(ux) if ux else 1e9,
                 (H / 2 - M) / abs(uy) if uy else 1e9)
        pos = (W / 2 + ux * sc, H / 2 + uy * sc)
        ax, ay = ux, uy

    px_, py_ = pos
    tip = (px_ + ax * 16, py_ + ay * 16)
    left = (px_ - ax * 8 - ay * 10, py_ - ay * 8 + ax * 10)
    right = (px_ - ax * 8 + ay * 10, py_ - ay * 8 - ax * 10)
    pygame.draw.polygon(surface, color, [tip, left, right])
    pygame.draw.polygon(surface, (20, 20, 20), [tip, left, right], 2)

    text = _font.render(f"{label}  {int(dist / config.TILE_SIZE)} tiles", True, color)
    tx_ = min(max(px_ - text.get_width() / 2, 6), W - text.get_width() - 6)
    ty_ = min(max(py_ + 20, 6), H - text.get_height() - 6)
    surface.blit(text, (tx_, ty_))