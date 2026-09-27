"""
Minimal pub/sub event bus.

Phase 1 uses this for decoupling (loot drops, XP gain, deaths) and it
doubles as the foundation for Phase 4's PlayerHistory event logging --
history/event_log.py will simply subscribe to the same bus later.
"""
from collections import defaultdict


class EventBus:
    def __init__(self):
        self._subscribers = defaultdict(list)
        self.log = []  # simple in-memory log, capped

    def subscribe(self, event_type: str, callback):
        self._subscribers[event_type].append(callback)

    def emit(self, event_type: str, **payload):
        self.log.append((event_type, payload))
        if len(self.log) > 2000:
            self.log.pop(0)
        for cb in self._subscribers.get(event_type, []):
            cb(**payload)