"""
Minimal pub/sub event bus.

Phase 1 uses this for decoupling (loot drops, XP gain, deaths) and it
doubles as the foundation for Phase 4's PlayerHistory event logging --
history/event_log.py will simply subscribe to the same bus later.
"""
from collections import defaultdict, deque
import inspect


class EventBus:
    def __init__(self, log_size: int = 2000):
        self._subscribers = defaultdict(list)
        self.log = deque(maxlen=log_size)  # O(1) performance via deque

    def subscribe(self, event_type: str, callback):
        self._subscribers[event_type].append(callback)

    def emit(self, event_type: str, **payload):
        self.log.append((event_type, payload))
        for cb in self._subscribers.get(event_type, []):
            try:
                cb(**payload)
            except TypeError:
                sig = inspect.signature(cb)
                # If the callback explicitly accepts **kwargs, the TypeError is real
                if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                    raise
                # Otherwise, filter payload kwargs down to what the handler expects
                filtered = {k: v for k, v in payload.items() if k in sig.parameters}
                cb(**filtered)