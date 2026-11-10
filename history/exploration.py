# [PROJECT: history]
import core.config as config


class ExplorationTracker:
    SAMPLE_INTERVAL = 0.25

    def __init__(self):
        self.visited: dict[str, set] = {}
        self.area_totals: dict[str, int] = {}
        self._timer = 0.0
        # Phase 2: room-level tracking
        self.region_rooms: dict[str, list] = {}
        self.rooms_visited: dict[str, set] = {}
        self.rooms_secret: dict[str, set] = {}

    # ---- existing API -------------------------------------------------
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

    def register_region(self, area_id: str, total_walkable: int):
        if area_id in self.area_totals:
            return
        self.area_totals[area_id] = max(1, total_walkable)
        self.visited.setdefault(area_id, set())

    # ---- Phase 2: room tracking API -----------------------------------
    def register_region_rooms(self, rid: str, room_rects,
                              secret_indices=()):
        self.region_rooms[rid] = [tuple(r) for r in room_rects]
        self.rooms_visited.setdefault(rid, set())
        self.rooms_secret[rid] = set(secret_indices)

    def mark_room_visited(self, rid: str, room_index: int) -> bool:
        """Mark a room as visited. Returns True if it is the first time."""
        seen = self.rooms_visited.setdefault(rid, set())
        first = room_index not in seen
        seen.add(room_index)
        return first

    def rooms_discovered_for(self, rid: str) -> int:
        return len(self.rooms_visited.get(rid, ()))

    def rooms_total_for(self, rid: str) -> int:
        return len(self.region_rooms.get(rid, ()))

    def rooms_fraction(self, rid: str) -> float:
        total = self.rooms_total_for(rid)
        return (self.rooms_discovered_for(rid) / total) if total else 0.0

    def room_index_at_tile(self, rid: str, col: int, row: int):
        for i, (x0, y0, x1, y1) in enumerate(self.region_rooms.get(rid, ())):
            if x0 <= col < x1 and y0 <= row < y1:
                return i
        return None

    # ---- serialization -------------------------------------------------
    def to_dict(self):
        return {
            "visited": {aid: list(coords) for aid, coords in self.visited.items()},
            "area_totals": self.area_totals,
            "region_rooms": {
                rid: [list(r) for r in rooms]
                for rid, rooms in self.region_rooms.items()
            },
            "rooms_visited": {
                rid: sorted(idxs) for rid, idxs in self.rooms_visited.items()
            },
            "rooms_secret": {
                rid: sorted(idxs) for rid, idxs in self.rooms_secret.items()
            },
        }

    @staticmethod
    def from_dict(d):
        tracker = ExplorationTracker()
        tracker.area_totals = d.get("area_totals", {})
        tracker.visited = {
            aid: set(tuple(c) for c in coords)
            for aid, coords in d.get("visited", {}).items()
        }
        tracker.region_rooms = {
            rid: [tuple(r) for r in rooms]
            for rid, rooms in d.get("region_rooms", {}).items()
        }
        tracker.rooms_visited = {
            rid: set(idxs) for rid, idxs in d.get("rooms_visited", {}).items()
        }
        tracker.rooms_secret = {
            rid: set(idxs) for rid, idxs in d.get("rooms_secret", {}).items()
        }
        return tracker