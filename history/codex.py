"""
CodexManager: account-wide achievement/milestone system.

Persists to codex.json (parallel to stash.json) so progress carries
across characters and worlds. Each unlocked achievement grants a small
permanent bonus, applied to every character's effective_stats during
recalc (idempotent, so repeated recalc doesn't stack).
"""
import json
import os
from collections import defaultdict

CODEX_FILE = "codex.json"

ACHIEVEMENTS = {
    "first_blood": {
        "name": "First Blood", "description": "Slay your first enemy.",
        "bonus": {"max_hp": 5}, "target": 1, "event": "enemy_killed",
    },
    "commander_slayer_1": {
        "name": "Head of the Snake", "description": "Slay your first pack Commander.",
        "bonus": {"base_damage": 2}, "target": 1, "event": "commander_killed",
    },
    "commander_slayer_10": {
        "name": "Warlord's Bane", "description": "Slay 10 pack Commanders.",
        "bonus": {"base_damage": 3, "max_hp": 10}, "target": 10,
        "event": "commander_killed",
    },
    "technique_master": {
        "name": "Kinetic Master", "description": "Discover all kinetic techniques.",
        "bonus": {"crit_chance": 0.03}, "special": "all_techniques",
    },
    "flawless_guardian": {
        "name": "Flawless Guardian",
        "description": "Defeat a boss without dying during the attempt.",
        "bonus": {"armor": 5}, "special": "flawless_boss",
    },
    "nemesis_slayer_3": {
        "name": "Rival's End", "description": "Defeat 3 Nemeses.",
        "bonus": {"max_hp": 15}, "target": 3, "event": "nemesis_slain",
    },
    "overcharge_veteran": {
        "name": "Overcharged Veteran", "description": "Enter 3 overcharged gates.",
        "bonus": {"max_hp": 10, "base_damage": 2}, "target": 3,
        "event": "overcharge_entered",
    },
    "boss_phase_survivor": {
        "name": "Through the Fire",
        "description": "Survive a full boss attempt (all phases).",
        "bonus": {"resist_chaos": 0.02, "resist_fire": 0.02},
        "special": "survived_full_boss",
    },
    "guardian_10": {
        "name": "Region Breaker", "description": "Defeat 10 region guardians.",
        "bonus": {"max_hp": 20, "armor": 3}, "target": 10, "event": "boss_killed",
    },
    "pressure_survivor": {
        "name": "Corruption Survivor",
        "description": "Kill a guardian while its region is Corrupted.",
        "bonus": {"max_hp": 10}, "special": "pressure_kill",
    },
}

KINETIC_TECHNIQUES_ALL = {
    "reverse_slash", "dash_strike", "intercept",
    "retreat_shot", "perfect_range", "heavy_reversal",
}


class CodexManager:
    def __init__(self, path=CODEX_FILE):
        self.path = path
        self.unlocked = set()
        self.progress = defaultdict(int)
        self.techniques_discovered = set()
        self._load()

    # ---------- persistence ----------
    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[CodexManager] Failed to load '{self.path}': {e}")
            return
        if not isinstance(data, dict):
            return
        self.unlocked = set(data.get("unlocked", []))
        self.progress = defaultdict(int, data.get("progress", {}))
        self.techniques_discovered = set(data.get("techniques", []))

    def save(self):
        data = {
            "unlocked": sorted(self.unlocked),
            "progress": dict(self.progress),
            "techniques": sorted(self.techniques_discovered),
        }
        try:
            with open(self.path, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            print(f"[CodexManager] Failed to save '{self.path}': {e}")

    # ---------- event recording ----------
    def record(self, event_type, amount=1, context=None):
        """Returns list of newly unlocked achievement ids."""
        context = context or {}
        newly = []

        for aid, ach in ACHIEVEMENTS.items():
            if aid in self.unlocked:
                continue
            if ach.get("event") == event_type:
                self.progress[aid] += amount
                if self.progress[aid] >= ach["target"]:
                    self.unlocked.add(aid)
                    newly.append(aid)

        if event_type == "kinetic_technique":
            name = context.get("name")
            if name:
                self.techniques_discovered.add(name)
                if (self.techniques_discovered >= KINETIC_TECHNIQUES_ALL
                        and "technique_master" not in self.unlocked):
                    self.unlocked.add("technique_master")
                    newly.append("technique_master")

        if event_type == "flawless_boss" and "flawless_guardian" not in self.unlocked:
            self.unlocked.add("flawless_guardian")
            newly.append("flawless_guardian")

        if event_type == "survived_full_boss" and "boss_phase_survivor" not in self.unlocked:
            self.unlocked.add("boss_phase_survivor")
            newly.append("boss_phase_survivor")

        if event_type == "pressure_kill" and "pressure_survivor" not in self.unlocked:
            self.unlocked.add("pressure_survivor")
            newly.append("pressure_survivor")

        #if newly:
        #    self.save()
        self.save()
        return newly

    def achievement_info(self, aid):
        return ACHIEVEMENTS.get(aid, {})

    def progress_for(self, aid):
        ach = ACHIEVEMENTS.get(aid, {})
        return self.progress.get(aid, 0), ach.get("target", 1)

    # ---------- bonus application ----------
    def aggregate_bonuses(self):
        agg = defaultdict(float)
        for aid in self.unlocked:
            ach = ACHIEVEMENTS.get(aid)
            if not ach:
                continue
            for k, v in ach.get("bonus", {}).items():
                agg[k] += v
        return dict(agg)

    def apply_bonuses_to_stats(self, stats):
        """Called from Player.recalc_stats — additive, idempotent."""
        agg = self.aggregate_bonuses()
        for k, v in agg.items():
            if k == "max_hp":
                stats.max_hp += int(v)
            elif k == "armor":
                stats.armor += int(v)
            elif k == "base_damage":
                stats.base_damage += int(v)
            elif k == "crit_chance":
                stats.crit_chance += float(v)
            elif k.startswith("resist_"):
                dtype = k.replace("resist_", "")
                stats.resistances[dtype] = stats.resistances.get(dtype, 0.0) + float(v)