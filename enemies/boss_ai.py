# [PROJECT: enemies]
"""
Boss AI director.

Sits *above* the Boss's existing state machine.  The Boss still owns
`_state` (idle / telegraph / recover) and `_execute_attack`.  The
director decides:

  - WHICH tactic to run this window (pressure / zone / combo / bait)
  - WHICH actions to weight in _choose_action
  - Whether this particular telegraph should be a FEINT
  - Whether the last hit should CHAIN into another attack

Tactics are chosen by observing the player:
  - kiter (stays far)     -> "pressure" (close the gap aggressively)
  - rusher (stays close)  -> "zone"     (push away, use projectiles)
  - jumpy (dodges often)  -> "bait"     (feint a telegraph)
  - neutral               -> random weighted among the above + "combo"
"""
import math
import random

from core.difficulty import D
import core.config as config
from enemies.ai_intelligence import PlayerObserver


TACTIC_DURATION_MIN = 3.5
TACTIC_DURATION_MAX = 6.0

FEINT_BASE_CHANCE   = 0.10    # at 0% difficulty
FEINT_DIFF_SPAN     = 0.35    # +up to 0.35 at 100% difficulty

COMBO_CHANCE_BASE   = 0.30
COMBO_CHANCE_SPAN   = 0.35

JUMPY_DASH_INTERVAL = 1.8     # avg dash interval under this = "jumpy"
KITE_DIST           = 260.0   # average distance above this = "kiter"
RUSH_DIST           = 100.0   # average distance below this = "rusher"


class BossAIDirector:
    def __init__(self, boss):
        self.boss = boss
        self.observer = PlayerObserver()
        self.tactic = "neutral"
        self.tactic_timer = 0.0          # forces a tactic pick on first update
        self.feint_pending = False
        self.chain_queue = 0
        self._recent_actions = []        # last few (action_name,) tuples

    # ---------------- per-frame ----------------
    def update(self, dt, player, dist: float):
        if not getattr(config, "AI_BOSS_DIRECTOR_ENABLED", True):
            return
        self.observer.update(dt, player)
        self.tactic_timer -= dt
        if self.tactic_timer <= 0:
            self._pick_tactic(player, dist)
            self.tactic_timer = random.uniform(TACTIC_DURATION_MIN,
                                               TACTIC_DURATION_MAX)

    # ---------------- tactic selection ----------------
    def _pick_tactic(self, player, dist):
        if not getattr(config, "AI_BOSS_TACTIC_SWITCH", True):
            return
        obs = self.observer

        # Jumpy player (dodges often, gaps < 1.8s) -> bait
        if len(obs.dash_intervals) >= 3:
            avg = sum(obs.dash_intervals) / len(obs.dash_intervals)
            if avg < JUMPY_DASH_INTERVAL:
                self.tactic = "bait"
                return

        # Distance tells us kiter / rusher
        if dist > KITE_DIST:
            self.tactic = "pressure"
            return
        if dist < RUSH_DIST:
            self.tactic = "zone"
            return

        # Neutral -> random weighted
        self.tactic = random.choices(
            ["pressure", "zone", "combo"],
            weights=[3, 2, 2],
        )[0]

    # ---------------- action weighting ----------------
    def pick_action_weights(self, dist: float, phase) -> dict:
        """
        Return weight multipliers for each candidate action.  The Boss's
        _choose_action uses these via random.choices.
        """
        w = {"melee": 1.0, "dash": 1.0, "ranged": 1.0, "blink": 1.0}

        if not getattr(phase, "enables_dash", False):
            w["dash"] = 0.0
        if not getattr(phase, "enables_blink", False):
            w["blink"] = 0.0

        if self.tactic == "pressure":
            w["dash"] *= 2.2
            w["melee"] *= 1.4
        elif self.tactic == "zone":
            w["ranged"] *= 2.5
            if dist > 220:
                w["dash"] *= 0.6
        elif self.tactic == "combo":
            w["dash"] *= 1.8
            w["melee"] *= 2.0
        elif self.tactic == "bait":
            w["melee"] *= 1.8
            w["dash"] *= 1.3

        # Distance overrides
        if dist < 90:
            w["ranged"] *= 0.25
        if dist > 250:
            w["melee"] *= 0.25

        # Defensive fallback - if everything went to zero, melee at least
        if sum(w.values()) <= 0.0:
            return {"melee": 1.0}
        return w

    # ---------------- feint ----------------
    def on_telegraph_started(self, action: str):
        self.feint_pending = False
        self._recent_actions.append(action)
        if len(self._recent_actions) > 6:
            self._recent_actions.pop(0)

        if not getattr(config, "AI_BOSS_FEINTS", True):
            return
        if self.tactic != "bait":
            return
        # Only feint when already damaged (phase 2+).
        if getattr(self.boss, "phase_index", 0) < 1:
            return

        chance = FEINT_BASE_CHANCE + FEINT_DIFF_SPAN * (D.percent / 100.0)
        if random.random() < chance:
            self.feint_pending = True

    def should_cancel_telegraph(self, player) -> bool:
        """A feint cancels the instant the player commits to a dash."""
        if not self.feint_pending:
            return False
        if player._dash_timer > 0:
            self.feint_pending = False
            return True
        return False

    # ---------------- combo chain ----------------
    def on_hit_landed(self):
        if not getattr(config, "AI_BOSS_COMBOS", True):
            return
        if self.tactic != "combo":
            return
        chance = COMBO_CHANCE_BASE + COMBO_CHANCE_SPAN * (D.percent / 100.0)
        if random.random() < chance:
            self.chain_queue = 1

    def consume_chain(self) -> bool:
        if self.chain_queue > 0:
            self.chain_queue -= 1
            return True
        return False