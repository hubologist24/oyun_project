import pygame
import core.config as config

FLOOR, WALL, DOOR_CLOSED, DOOR_OPEN = 0, 1, 2, 3


class TileMap:
    def __init__(self, grid):
        self.grid = grid
        self.rows = len(grid)
        self.cols = len(grid[0]) if self.rows else 0
        self.tile_size = config.TILE_SIZE

    def is_wall(self, col, row) -> bool:
        if col < 0 or row < 0 or col >= self.cols or row >= self.rows:
            return True
        return self.grid[row][col] in (WALL, DOOR_CLOSED)

    def ensure_size(self, cols, rows):
        """Grow the grid (padding with wall). Never shrinks."""
        cols = max(cols, self.cols)
        for row in self.grid:
            if len(row) < cols:
                row.extend([WALL] * (cols - len(row)))
        while len(self.grid) < rows:
            self.grid.append([WALL] * cols)
        self.rows = len(self.grid)
        self.cols = len(self.grid[0]) if self.rows else 0

    def collides_rect(self, rect: pygame.Rect) -> bool:
        ts = self.tile_size
        left, right = rect.left // ts, (rect.right - 1) // ts
        top, bottom = rect.top // ts, (rect.bottom - 1) // ts
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
                tile = self.grid[row][col]
                screen_rect = camera.world_to_screen_rect(
                    pygame.Rect(col * ts, row * ts, ts, ts))
                if tile == WALL:
                    pygame.draw.rect(surface, config.COLOR_WALL, screen_rect)
                elif tile == DOOR_CLOSED:
                    pygame.draw.rect(surface, (120, 78, 36), screen_rect)
                    pygame.draw.rect(surface, (200, 150, 70), screen_rect, 3)
                    key = screen_rect.inflate(-ts * 0.6, -ts * 0.6)
                    pygame.draw.rect(surface, (30, 20, 10), key)
                else:
                    pygame.draw.rect(surface, config.COLOR_FLOOR, screen_rect)
                    if tile == DOOR_OPEN:
                        pygame.draw.rect(surface, (90, 230, 255), screen_rect, 3)