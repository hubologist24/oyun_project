"""
Persistent, structured event log. Complements core/events.EventBus's
in-memory capped log (which exists for cheap debugging) with a
durable, typed record intended for aggregation and save/load.

Subscribes to the EventBus for every event type we care about and
converts each into a compact HistoryEvent record. Per spec: this is
NOT meant to be sent to any AI wholesale -- history/aggregator.py
reduces it into a small PlayerProfile instead.
"""
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class HistoryEvent:
    event_type: str
    timestamp: float
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {"event_type": self.event_type, "timestamp": self.timestamp, "payload": self.payload}

    @staticmethod
    def from_dict(d):
        return HistoryEvent(event_type=d["event_type"], timestamp=d["timestamp"], payload=d.get("payload", {}))


TRACKED_EVENTS = [
    "level_up",
    "item_found",
    "item_equipped",
    "item_sold",
    "item_destroyed",
    "enemy_killed",
    "player_hit",
    "player_death",
    "player_attack",
    "boss_attempt_started",
    "boss_attempt_ended",
    "boss_killed",
    "extension_generated",
    "area_entered",
    "skill_used",
]


class EventLog:
    """
    Wraps an EventBus: subscribes to every tracked event type and stores
    a durable, serializable history. Capped to avoid unbounded save-file
    growth over very long play sessions (per spec's aggregation-first
    philosophy -- old raw events matter far less than the aggregate).
    """
    MAX_EVENTS = 20000

    def __init__(self, event_bus, max_events: int = MAX_EVENTS):
        self.events: List[HistoryEvent] = []
        self.max_events = max_events
        self._bus = event_bus
        self._attach()

    def _attach(self):
        for event_type in TRACKED_EVENTS:
            # bind event_type via default-arg to avoid late-binding closure bug
            self._bus.subscribe(event_type, lambda event_type=event_type, **payload:
                                 self._record(event_type, payload))

    def _record(self, event_type: str, payload: dict):
        self.events.append(HistoryEvent(event_type=event_type, timestamp=time.time(), payload=payload))
        if len(self.events) > self.max_events:
            # Drop oldest 10% rather than one-by-one, cheaper amortized cost.
            trim = self.max_events // 10
            self.events = self.events[trim:]

    def events_of_type(self, event_type: str) -> List[HistoryEvent]:
        return [e for e in self.events if e.event_type == event_type]

    def to_dict(self):
        return {"events": [e.to_dict() for e in self.events]}

    @staticmethod
    def from_dict(d, event_bus):
        log = EventLog(event_bus, max_events=EventLog.MAX_EVENTS)
        log.events = [HistoryEvent.from_dict(e) for e in d.get("events", [])]
        return log