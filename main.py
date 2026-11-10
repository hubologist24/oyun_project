# [PROJECT: .] [ENTRY POINT]
import sys
import os
import argparse

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


def _run_level_prompt(screen, clock):
    from ui.character_creator import CharacterCreatorUI
    creator = CharacterCreatorUI()
    creator.show()
    while creator.visible:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return -1
            if ev.type == pygame.KEYDOWN:
                result = creator.handle_key(ev.key)
                if result is not None:
                    return result
        creator.draw(screen)
        pygame.display.flip()
        clock.tick(60)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=None,
                        help="Shared world seed (omit for a random one).")
    args, _ = parser.parse_known_args()

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

            from save.save_manager import SaveManager
            is_fresh = not SaveManager(slot_id=slot_id).exists()

            starting_level = None
            if is_fresh:
                starting_level = _run_level_prompt(screen, clock)
                if starting_level == -1:
                    break

            # Only apply --seed on a brand-new character; loaded saves keep
            # their stored world seed.
            seed_for_game = args.seed if is_fresh else None

            game = Game(slot_id=slot_id, starting_level=starting_level,
                        world_seed=seed_for_game)
            game.run()
    finally:
        pygame.quit()


if __name__ == "__main__":
    main()