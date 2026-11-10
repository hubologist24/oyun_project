# [PROJECT: ui]
import pygame
import core.config as config
from world.gate_seals import SEAL_COLORS, seal_progress_text


_font_title = None
_font_body = None
_font_small = None


def _init_fonts():
    global _font_title, _font_body, _font_small
    if _font_title is None:
        _font_title = pygame.font.SysFont("consolas", 16, bold=True)
        _font_body = pygame.font.SysFont("consolas", 13)
        _font_small = pygame.font.SysFont("consolas", 12)


def draw_gate_seal_widget(surface, game, show_detail=False, multi_gate_hint=0):
    _init_fonts()
    gate = game.world.frontier_gate()
    if gate is None:
        return
    seal = getattr(gate, "seal", None) or {}
    if not seal:
        return

    locked_by_level = (gate.level_requirement > game.player.progression.level)

    w = 320
    x = 16
    y = config.SCREEN_HEIGHT - 120
    h = 96 if show_detail else 58
    if multi_gate_hint:
        h += 16
    bg = pygame.Surface((w, h), pygame.SRCALPHA)
    bg.fill((10, 10, 16, 200))
    surface.blit(bg, (x, y))
    color = SEAL_COLORS.get(seal.get("type"), (200, 200, 200))
    if seal.get("ready"):
        color = (120, 255, 140)
    pygame.draw.rect(surface, color, (x, y, w, h), 2, border_radius=4)

    if locked_by_level:
        title = f"◇ SEALED — Level {gate.level_requirement} required"
        surface.blit(_font_title.render(title, True, (200, 180, 120)), (x + 8, y + 6))
        sub = f"You are level {game.player.progression.level}."
        surface.blit(_font_small.render(sub, True, (160, 160, 170)), (x + 8, y + 28))
        return

    from world.gate_tiers import GATE_TIERS
    tier = GATE_TIERS.get(getattr(gate, "tier", "standard"),
                           GATE_TIERS["standard"])

    title = f"{seal.get('label','Seal')}"
    tier_tag = f"   [{tier['label']}]"
    if seal.get("curse"):
        title += "  [CURSED]"
    surface.blit(_font_title.render(title, True, color), (x + 8, y + 6))
    surface.blit(_font_small.render(tier_tag, True, tier["color"]),
                 (x + 8 + _font_title.size(title)[0], y + 6))
    surface.blit(_font_small.render(seal.get("desc", ""), True, (200, 200, 210)),
                 (x + 8, y + 26))

    ptext = seal_progress_text(seal)
    surface.blit(_font_body.render(ptext, True, color), (x + 8, y + 42))

    target = seal.get("target", 1) or 1
    prog = seal.get("progress", 0)
    frac = min(1.0, prog / target) if target else 0.0
    bx, by, bw, bh = x + 140, y + 44, 168, 8
    pygame.draw.rect(surface, (40, 40, 50), (bx, by, bw, bh))
    pygame.draw.rect(surface, color, (bx, by, int(bw * frac), bh))

    if show_detail and seal.get("curse_label"):
        surface.blit(_font_small.render(seal["curse_label"], True, (230, 120, 120)),
                     (x + 8, y + 68))

    if multi_gate_hint:
        hint = _font_small.render(
            f"+{multi_gate_hint - 1} more gate{'s' if multi_gate_hint > 2 else ''}"
            f" — F11 to choose",
            True, (160, 180, 220))
        surface.blit(hint, (x + 8, y + h - 18))