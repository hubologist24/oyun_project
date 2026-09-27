"""
Lightweight exploration tracker: samples the player's current tile
position at a low frequency (not every frame) and records visited
walkable tiles per area. Used to compute PlayerProfile.exploration.

Kept intentionally simple: a set of (area_id, col, row) tuples. This
is cheap enough to keep in memory for very long sessions (a fully
explored 50x38 area is at most 1900 tiles).
"""
import core.config as config


class ExplorationTracker:
    SAMPLE_INTERVAL = 0.25  # seconds between position samples

    def __init__(self):
        self.visited: dict[str, set] = {}   # area_id -> set of (col, row)
        self.area_totals: dict[str, int] = {}  # area_id -> total walkable tile count
        self._timer = 0.0

    def register_area(self, area_id: str, tilemap):
        if area_id in self.area_totals:
            return
        total = sum(1 for row in tilemap.grid for tile in row if tile == 0)
        self.area_totals[area_id] = max(1, total)
        self.visited.setdefault(area_id, set())

    def update(self, dt, area_id: str, player_x: float, player_y: float):
        self._timer += dt
        if self._timer < self.SAMPLE_INTERVAL:
            return
        self._timer = 0.0
        ts = config.TILE_SIZE
        col, row = int(player_x // ts), int(player_y // ts)
        self.visited.setdefault(area_id, set()).add((col, row))

    def fraction_for(self, area_id: str) -> float:
        total = self.area_totals.get(area_id, 0)
        if total <= 0:
            return 0.0
        visited = len(self.visited.get(area_id, set()))
        return min(1.0, visited / total)

    def current_fraction(self, area_id: str = None) -> float:
        if area_id is None:
            if not self.area_totals:
                return 0.0
            area_id = list(self.area_totals.keys())[-1]
        return self.fraction_for(area_id)

    def average_fraction(self) -> float:
        if not self.area_totals:
            return 0.0
        fractions = [self.fraction_for(aid) for aid in self.area_totals]
        return sum(fractions) / len(fractions)

    def to_dict(self):
        return {
            "visited": {aid: list(coords) for aid, coords in self.visited.items()},
            "area_totals": self.area_totals,
        }

    @staticmethod
    def from_dict(d):
        tracker = ExplorationTracker()
        tracker.area_totals = d.get("area_totals", {})
        tracker.visited = {aid: set(tuple(c) for c in coords) for aid, coords in d.get("visited", {}).items()}
        return tracker