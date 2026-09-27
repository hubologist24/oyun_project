"""
Entry point for the AI-Evolving ARPG prototype.
Phase 1: core movement, combat, loot, leveling, save/load, starting boss.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.game import Game


def main():
    game = Game()
    game.run()


if __name__ == "__main__":
    main()