"""
Simple follow-camera converting world coordinates to screen coordinates.
"""
import pygame
import core.config as config


import random


class Camera:
    def __init__(self, world_w, world_h):
        self.x = 0
        self.y = 0
        self.world_w = world_w
        self.world_h = world_h
        self._shake_time = 0.0
        self._shake_mag = 0.0
        self._shake_dx = 0
        self._shake_dy = 0

    def update(self, target_x, target_y):
        self.x = target_x - config.SCREEN_WIDTH / 2
        self.y = target_y - config.SCREEN_HEIGHT / 2
        self.x = max(0, min(self.x, max(0, self.world_w - config.SCREEN_WIDTH)))
        self.y = max(0, min(self.y, max(0, self.world_h - config.SCREEN_HEIGHT)))

    def shake(self, magnitude: float, duration: float):
        # Additive cap so several hits in a row don't stack unbounded.
        self._shake_mag = max(self._shake_mag, magnitude)
        self._shake_time = max(self._shake_time, duration)

    def tick(self, dt: float):
        """Advance shake decay. Called once per frame with real dt."""
        if self._shake_time <= 0:
            self._shake_dx = self._shake_dy = 0
            return
        self._shake_time -= dt
        if self._shake_time <= 0:
            self._shake_mag = 0.0
            self._shake_dx = self._shake_dy = 0
            return
        mag = int(self._shake_mag)
        if mag <= 0:
            self._shake_dx = self._shake_dy = 0
            return
        self._shake_dx = random.randint(-mag, mag)
        self._shake_dy = random.randint(-mag, mag)

    def world_to_screen(self, pos):
        return (int(pos[0] - self.x + self._shake_dx),
                int(pos[1] - self.y + self._shake_dy))

    def world_to_screen_rect(self, rect: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(int(rect.x - self.x + self._shake_dx),
                            int(rect.y - self.y + self._shake_dy),
                            rect.width, rect.height)

    def view_rect(self) -> pygame.Rect:
        return pygame.Rect(int(self.x), int(self.y),
                            config.SCREEN_WIDTH, config.SCREEN_HEIGHT)

    def screen_to_world(self, screen_pos):
        # Subtract the shake offset so world-space cursor position is
        # unchanged by the visual jitter.
        return (screen_pos[0] + self.x - self._shake_dx,
                screen_pos[1] + self.y - self._shake_dy)