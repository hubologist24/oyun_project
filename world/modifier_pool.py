"""Persistent, mean-reverting pool of world modifiers.

Each new region triggers one `step()` that adds/removes 1-3 modifiers.
The pool has a hard floor, a soft target, and a hard ceiling.
Modifiers carry a `polarity` of "buff", "curse", or "neutral" so the
pool never drifts to all-blessings or all-curses.
"""

import copy
import json
import os
import random
from typing import List

DEFAULT_TARGET  = 10
DEFAULT_FLOOR   = 5
DEFAULT_CEILING = 18
DEFAULT_DRIFT   = 0.8
BLESSING_RATIO  = 0.40

_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data",
                             "world_modifier_pool.json")


def load_catalog():
    """Returns (blessings, curses) — two lists of modifier dicts."""
    with open(_CATALOG_PATH, "r") as f:
        data = json.load(f)
    return list(data.get("blessings", [])), list(data.get("curses", []))


class ModifierPool:
    def __init__(self, modifiers=None,
                 target=DEFAULT_TARGET,
                 floor=DEFAULT_FLOOR,
                 ceiling=DEFAULT_CEILING,
                 drift=DEFAULT_DRIFT):
        self.modifiers: List[dict] = list(modifiers or [])
        self.target  = target
        self.floor   = floor
        self.ceiling = ceiling
        self.drift   = drift
        self.step_log: List[dict] = []

    # ---------- queries ----------

    def size(self):
        return len(self.modifiers)

    def polarity_counts(self):
        buffs  = sum(1 for m in self.modifiers if m.get("polarity") == "buff")
        curses = sum(1 for m in self.modifiers if m.get("polarity") == "curse")
        return {"buff": buffs, "curse": curses,
                "neutral": self.size() - buffs - curses}

    # ---------- walk step ----------

    def step(self, rng, blessings, curses) -> dict:
        size_before = self.size()
        added, removed = [], []

        num_changes = rng.choices([1, 2, 3, 4], weights=[2, 4, 3, 1])[0]

        for _ in range(num_changes):
            n = self.size()
            can_remove = n > self.floor
            can_add    = n < self.ceiling

            if not can_remove and not can_add:
                break

            if not can_remove:                       # forced add
                if not self._add(added, rng, blessings, curses):
                    break                            # catalog exhausted — done
                continue

            if not can_add:                          # forced remove
                self._remove(removed, rng)
                continue

            span  = max(1, self.ceiling - self.floor)
            p_add = 0.5 + self.drift * (self.target - n) / span
            p_add = max(0.10, min(0.90, p_add))

            if rng.random() < p_add:
                if not self._add(added, rng, blessings, curses) and can_remove:
                    self._remove(removed, rng)       # fall back to remove
            else:
                self._remove(removed, rng)

        summary = {"size_before": size_before, "size_after": self.size(),
                   "added": added, "removed": removed}
        self.step_log.append(summary)
        self.step_log = self.step_log[-30:]
        return summary

    # ---------- internals ----------

    def _add(self, added, rng, blessings, curses) -> bool:
        """Returns True if a modifier was actually added."""
        polarity = self._decide_polarity(rng)
        pool = blessings if polarity == "buff" else curses
        if not pool:
            pool = blessings or curses
            polarity = "buff" if pool is blessings else "curse"
        if not pool:
            return False

        existing = {m.get("id") or m.get("name") for m in self.modifiers}
        fresh = [m for m in pool
                 if (m.get("id") or m.get("name")) not in existing]
        fresh = [m for m in fresh
                 if not self._is_canceling(self.modifiers, m)]
        if not fresh:
            return False

        chosen = copy.deepcopy(rng.choice(fresh))
        chosen["polarity"] = polarity
        self.modifiers.append(chosen)
        added.append(chosen)
        return True

    def _remove(self, removed, rng):
        if not self.modifiers:
            return
        # 70 % oldest-first, 30 % random
        if rng.random() < 0.3 and len(self.modifiers) > 1:
            idx = rng.randrange(len(self.modifiers))
        else:
            idx = 0
        removed.append(self.modifiers.pop(idx))

    def _decide_polarity(self, rng) -> str:
        n = self.size()
        if n == 0:
            return "curse" if rng.random() < 0.6 else "buff"
        counts = self.polarity_counts()
        ratio  = counts["buff"] / n
        if counts["buff"] == 0:
            return "buff"
        if ratio < 0.25:
            return "buff"
        if ratio > 0.60:
            return "curse"
        return rng.choices(["buff", "curse"],
                           weights=[BLESSING_RATIO, 1 - BLESSING_RATIO])[0]

    @staticmethod
    def _is_canceling(existing, candidate):
        """True if candidate directly opposes any existing modifier's effect."""
        comparable = {"damage_taken_multiplier", "damage_dealt_multiplier"}
        for e_mod in existing:
            for e_rule in e_mod.get("rules", []):
                if e_rule.get("type") not in comparable:
                    continue
                for c_rule in candidate.get("rules", []):
                    if c_rule.get("type") != e_rule.get("type"):
                        continue
                    if (e_rule.get("target") != c_rule.get("target")
                            or e_rule.get("damage_type")
                            != c_rule.get("damage_type")):
                        continue
                    em = float(e_rule.get("multiplier", 1.0))
                    cm = float(c_rule.get("multiplier", 1.0))
                    if (em - 1.0) * (cm - 1.0) < 0:
                        return True
        return False

    # ---------- serialization ----------

    def to_dict(self):
        return {
            "modifiers": self.modifiers,
            "target":    self.target,
            "floor":     self.floor,
            "ceiling":   self.ceiling,
            "drift":     self.drift,
            "step_log":  self.step_log,
        }

    @staticmethod
    def from_dict(d):
        if not isinstance(d, dict):
            d = {}
        raw = d.get("modifiers")
        if not isinstance(raw, list):
            raw = []
        # Drop anything that isn't a dict-shaped modifier
        raw = [m for m in raw if isinstance(m, dict)]
        p = ModifierPool(
            modifiers=raw,
            target  = d.get("target",  DEFAULT_TARGET),
            floor   = d.get("floor",   DEFAULT_FLOOR),
            ceiling = d.get("ceiling", DEFAULT_CEILING),
            drift   = d.get("drift",   DEFAULT_DRIFT),
        )
        p.step_log = d.get("step_log", []) if isinstance(d.get("step_log"), list) else []
        return p