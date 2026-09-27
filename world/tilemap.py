"""
Simple grid-based tilemap with AABB collision.
1 = wall, 0 = floor. Phase 3 will replace the hardcoded layout with
procedurally generated grids using the same interface.
"""
import pygame
import core.config as config


class TileMap:
    def __init__(self, grid):
        self.grid = grid
        self.rows = len(grid)
        self.cols = len(grid[0]) if self.rows else 0
        self.tile_size = config.TILE_SIZE

    def is_wall(self, col, row) -> bool:
        if col < 0 or row < 0 or col >= self.cols or row >= self.rows:
            return True
        return self.grid[row][col] == 1

    def collides_rect(self, rect: pygame.Rect) -> bool:
        ts = self.tile_size
        left = rect.left // ts
        right = (rect.right - 1) // ts
        top = rect.top // ts
        bottom = (rect.bottom - 1) // ts
        for row in range(top, bottom + 1):
            for col in range(left, right + 1):
                if self.is_wall(col, row):
                    return True
        return False

    def pixel_size(self):
        return self.cols * self.tile_size, self.rows * self.tile_size

    def draw(self, surface, camera):
        ts = self.tile_size
        cam_rect = camera.view_rect()
        start_col = max(0, cam_rect.left // ts)
        end_col = min(self.cols, cam_rect.right // ts + 1)
        start_row = max(0, cam_rect.top // ts)
        end_row = min(self.rows, cam_rect.bottom // ts + 1)

        for row in range(start_row, end_row):
            for col in range(start_col, end_col):
                world_rect = pygame.Rect(col * ts, row * ts, ts, ts)
                screen_rect = camera.world_to_screen_rect(world_rect)
                color = config.COLOR_WALL if self.grid[row][col] == 1 else config.COLOR_FLOOR
                pygame.draw.rect(surface, color, screen_rect)