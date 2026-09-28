"""
Simple follow-camera converting world coordinates to screen coordinates.
"""
import pygame
import core.config as config


class Camera:
    def __init__(self, world_w, world_h):
        self.x = 0
        self.y = 0
        self.world_w = world_w
        self.world_h = world_h

    def update(self, target_x, target_y):
        self.x = target_x - config.SCREEN_WIDTH / 2
        self.y = target_y - config.SCREEN_HEIGHT / 2
        self.x = max(0, min(self.x, max(0, self.world_w - config.SCREEN_WIDTH)))
        self.y = max(0, min(self.y, max(0, self.world_h - config.SCREEN_HEIGHT)))

    def world_to_screen(self, pos):
        return int(pos[0] - self.x), int(pos[1] - self.y)

    def world_to_screen_rect(self, rect: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(int(rect.x - self.x), int(rect.y - self.y), rect.width, rect.height)

    def view_rect(self) -> pygame.Rect:
        return pygame.Rect(int(self.x), int(self.y), config.SCREEN_WIDTH, config.SCREEN_HEIGHT)

    def screen_to_world(self, screen_pos):
        return (screen_pos[0] + self.x, screen_pos[1] + self.y)