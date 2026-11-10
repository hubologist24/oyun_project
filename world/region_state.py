"""Persistent per-region memory."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict


@dataclass
class RegionState:
    region_id: str
    spawners_destroyed: bool = False
    corruption_cleared: bool = False
    shortcut_opened: bool = False
    elites_escaped: int = 0
    bosses_killed: int = 0
    convoys_killed: int = 0
    events_completed: list = field(default_factory=list)

    # Phase 5
    spawned_seal_content: bool = False
    convoy_escaped: bool = False
    convoy_respawn_at: float = 0.0

    def mark_event_completed(self, event_id: str):
        if event_id not in self.events_completed:
            self.events_completed.append(event_id)

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        known = {
            "region_id", "spawners_destroyed", "corruption_cleared",
            "shortcut_opened", "elites_escaped", "bosses_killed",
            "convoys_killed", "events_completed",
            "spawned_seal_content", "convoy_escaped", "convoy_respawn_at",
        }
        kwargs = {k: v for k, v in (d or {}).items() if k in known}
        if "region_id" not in kwargs:
            kwargs["region_id"] = (
                d.get("region_id", "unknown") if isinstance(d, dict) else "unknown")
        return RegionState(**kwargs)