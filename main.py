# [PROJECT: .] [ENTRY POINT]
# [SYMBOLS] functions: _run_character_select, main
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame
import core.config as config
from core.game import Game
from ui.character_select import CharacterSelect


def _run_character_select(screen, clock):
    select = CharacterSelect()
    select.open()
    while not select.is_done():
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return None
            if ev.type == pygame.KEYDOWN:
                select.handle_key(ev.key)
        select.draw(screen)
        pygame.display.flip()
        clock.tick(60)
    return select.result


def main():
    pygame.init()
    pygame.display.set_caption("AI-Evolving ARPG")
    pygame.display.set_mode((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
    clock = pygame.time.Clock()

    try:
        while True:
            screen = pygame.display.get_surface()
            slot_id = _run_character_select(screen, clock)
            if slot_id is None:
                break
            game = Game(slot_id=slot_id)
            game.run()
    finally:
        pygame.quit()


if __name__ == "__main__":
    main()