"""Shared constants and helpers for the Game mixins."""

MAX_ON_KILL_DEPTH = 3
CRIT_BURST_RADIUS = 70.0
CRIT_BURST_RATIO = 0.50

PRESSURE_THRESHOLDS = [0, 90, 180, 300, 450]      # seconds -> level 0..4
PRESSURE_LABELS = ["Calm", "Uneasy", "Restless", "Hostile", "Corrupted"]
PRESSURE_COLORS = [(150, 150, 150), (200, 200, 120), (220, 140, 80),
                   (255, 80, 80), (200, 40, 200)]


class _NullEventBus:
    """
    Absorbs subscriptions (and drops emits) while we rebuild an EventLog
    from a save. We only want the restored .events list -- not a fresh
    set of callbacks attached to the live bus.
    """
    def subscribe(self, *args, **kwargs):
        return None

    def unsubscribe(self, *args, **kwargs):
        return None

    def emit(self, *args, **kwargs):
        return None
