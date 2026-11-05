# [PROJECT: enemies]
"""
Shared AI intelligence primitives.

Enemies react to PLAYER STATE, not just player position:
  - punishes the window after the player dashes or attacks
  - predicts where the player will be (predictive aim)
  - flanks instead of charging head-on
  - retreats when wounded if allies are nearby
  - attack-timer jitter to prevent dogpiling

All tuning scales with the difficulty percentage from core.difficulty.D,
so "Nightmare" enemies are perceptibly smarter than "Easy" enemies.
"""
import math
import random

from core.difficulty import D
import core.config as config


# --- tuning (each is the value at 0% difficulty) ---------------------------
PUNISH_AFTER_DASH_BASE   = 0.22   # player vulnerable ~0.22s after dash ends
PUNISH_AFTER_ATTACK_BASE = 0.12   # player vulnerable ~0.12s after attack
PUNISH_DIFF_SPAN         = 0.20   # +up to 0.20s at 100% difficulty

LEAD_FACTOR_BASE         = 0.30   # how far ahead to aim at 0% difficulty
LEAD_DIFF_SPAN           = 0.35   # +up to 0.35 at 100% difficulty
LEAD_MAX_DIST            = 400.0  # don't bother predicting beyond this

FLANK_ANGLE_DEG          = 55.0   # how far off-axis to swing around
FLANK_MAX_OFFSET         = 120.0  # max world-unit side-step

RETREAT_HP_FRACTION      = 0.28   # below this, consider retreat
RETREAT_MIN_ALLIES       = 1      # only retreat if this many allies nearby
RETREAT_ALLY_RADIUS      = 300.0
RETREAT_SPEED_MULT       = 1.3

ATTACK_JITTER_MIN        = 0.85   # cooldown multiplier range for mob attacks
ATTACK_JITTER_MAX        = 1.25

# Behaviors that get the full tactical layer (0 = none, 1 = reactive, 2 = full)
_TIER_BY_BEHAVIOR = {
    "swarmer":  0,
    "chaser":   1,
    "brute":    1,
    "ambusher": 2,
    "circler":  2,
    "ranged":   2,
}


def _enabled() -> bool:
    return getattr(config, "AI_MOB_INTELLIGENCE_ENABLED", True)


def _diff_scale(base: float, span: float) -> float:
    """base at 0% difficulty, base+span at 100% difficulty."""
    return base + span * (D.percent / 100.0)


def intel_tier_for(behavior: str, is_elite: bool) -> int:
    """Elites get +1 tier; capped at 3."""
    t = _TIER_BY_BEHAVIOR.get(behavior, 1)
    if is_elite:
        t += 1
    return min(t, 3)


def attack_jitter() -> float:
    """Multiplier applied to post-attack cooldowns to break simultaneity."""
    if not getattr(config, "AI_MOB_ATTACK_JITTER", True):
        return 1.0
    return random.uniform(ATTACK_JITTER_MIN, ATTACK_JITTER_MAX)


# ---------------------------------------------------------------------------
class PlayerObserver:
    """
    Per-enemy observation of the player.  Lightweight (uses __slots__).

    Detects the start / end of the player's dash, the moment they swing,
    and the moment they get hit.  These transitions are what make
    "react to the player" possible without reading player internals every
    tick.
    """
    __slots__ = (
        "clock", "last_dash_start", "last_dash_end", "last_attack_time",
        "last_hit_time", "dash_intervals", "_prev_dash", "_prev_cd",
    )

    def __init__(self):
        self.clock = 0.0
        self.last_dash_start = -99.0
        self.last_dash_end = -99.0
        self.last_attack_time = -99.0
        self.last_hit_time = -99.0
        self.dash_intervals = []
        self._prev_dash = 0.0
        self._prev_cd = 0.0

    def update(self, dt: float, player):
        self.clock += dt

        # Dash start / end transitions
        dash_now = player._dash_timer
        if dash_now > 0 and self._prev_dash <= 0:
            gap = self.clock - self.last_dash_start
            if 0.2 < gap < 6.0:
                self.dash_intervals.append(gap)
                if len(self.dash_intervals) > 5:
                    self.dash_intervals.pop(0)
            self.last_dash_start = self.clock
        if dash_now <= 0 and self._prev_dash > 0:
            self.last_dash_end = self.clock
        self._prev_dash = dash_now

        # Attack: cooldown jumps upward the frame it starts
        cd_now = player._attack_cooldown_timer
        if cd_now > self._prev_cd + 0.05:
            self.last_attack_time = self.clock
        self._prev_cd = cd_now

        if player._last_hit_flash > 0:
            self.last_hit_time = self.clock

    def in_punish_window(self) -> bool:
        """True if the player is in a post-action vulnerable moment."""
        if not getattr(config, "AI_MOB_PUNISH_WINDOWS", True):
            return False
        d = _diff_scale(PUNISH_AFTER_DASH_BASE, PUNISH_DIFF_SPAN)
        a = _diff_scale(PUNISH_AFTER_ATTACK_BASE, PUNISH_DIFF_SPAN)
        since_dash = self.clock - self.last_dash_end
        since_attack = self.clock - self.last_attack_time
        return (0 <= since_dash <= d) or (0 <= since_attack <= a)

    def predict_player_position(self, player, delta_t: float) -> tuple:
        """Rough forward-extrapolation of the player's position."""
        if delta_t <= 0:
            return player.x, player.y
        speed = getattr(player.effective_stats, "speed", 200.0)
        if player.move_target is not None:
            dx = player.move_target.x - player.x
            dy = player.move_target.y - player.y
            d = math.hypot(dx, dy) or 1.0
            vx, vy = dx / d * speed, dy / d * speed
        else:
            vx = player.facing.x * speed
            vy = player.facing.y * speed
        lead = _diff_scale(LEAD_FACTOR_BASE, LEAD_DIFF_SPAN)
        return (player.x + vx * delta_t * lead,
                player.y + vy * delta_t * lead)


# ---------------------------------------------------------------------------
def flank_offset_point(from_x, from_y, target_x, target_y, side: int) -> tuple:
    """
    Return a point offset to the side of the target so the enemy
    approaches at an angle instead of straight-on.  `side` is +1 / -1.
    """
    dx = target_x - from_x
    dy = target_y - from_y
    d = math.hypot(dx, dy) or 1.0
    px, py = -dy / d, dx / d
    angle = math.radians(FLANK_ANGLE_DEG)
    offset = min(angle * d, FLANK_MAX_OFFSET)
    return (target_x + px * offset * side,
            target_y + py * offset * side)


# ---------------------------------------------------------------------------
def wants_retreat(hp: float, max_hp: float, nearby_allies: int) -> bool:
    """Low HP AND has allies around (retreating alone is just dying slowly)."""
    if not getattr(config, "AI_MOB_RETREAT", True):
        return False
    if max_hp <= 0 or hp / max_hp >= RETREAT_HP_FRACTION:
        return False
    return nearby_allies >= RETREAT_MIN_ALLIES