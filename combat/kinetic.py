"""
Kinetic techniques: contextual attack variations discovered through
movement, facing and timing. No skill tree -- the tracker watches what the
player is physically doing and tags hits with techniques.

Design rule: techniques change HOW a hit behaves (stagger, pierce, slow,
interrupt, expose), not just how hard it hits.
"""
import math
from collections import deque, Counter
from dataclasses import replace
from itertools import chain

REVERSE_ANGLE = math.radians(150)
REVERSE_WINDOW = 0.18
DASH_GRACE = 0.14
RETREAT_WINDOW = 0.45
NEAR_MISS_WINDOW = 0.7
PERFECT_RANGE_FRAC = 0.85
INTERCEPT_SPEED_MIN = 45.0
RETREAT_SPEED_MIN = 60.0
TECH_COOLDOWN = 0.6

TECHNIQUES = {
    "reverse_slash":  ("REVERSE SLASH",  (255, 200, 120)),
    "dash_strike":    ("DASH STRIKE",    (140, 230, 255)),
    "intercept":      ("INTERCEPT",      (255, 120, 120)),
    "retreat_shot":   ("RETREAT SHOT",   (150, 255, 170)),
    "perfect_range":  ("PERFECT RANGE",  (255, 240, 120)),
    "heavy_reversal": ("HEAVY REVERSAL", (255, 160, 80)),
}


class KineticTracker:
    def __init__(self, game):
        self.game = game
        self.reset()
        game.event_bus.subscribe("near_miss", self._on_near_miss)

    # ---------------- state ----------------
    def reset(self):
        self.clock = 0.0
        self.facing_hist = deque(maxlen=40)     # (time, angle)
        self.last_dash_active = -99.0
        self.last_retreat = -99.0
        self.last_near_miss = -99.0
        self.player_speed = 0.0
        self._last_pos = None
        self._prev_pos = {}
        self.enemy_vel = {}
        self._cd = {}
        self.stats = Counter()
        self.discovered = set()

    def restore_from_log(self, event_log):
        """Rebuild discovered techniques from the persisted event log."""
        for e in event_log.events_of_type("kinetic_technique"):
            n = e.payload.get("name")
            if n in TECHNIQUES:
                self.discovered.add(n)

    def _on_near_miss(self, **kw):
        self.last_near_miss = self.clock

    # ---------------- per-frame ----------------
    def update(self, dt):
        if dt <= 0:
            return
        self.clock += dt
        g = self.game
        p = g.player

        pvx = pvy = 0.0
        if self._last_pos is not None:
            pvx = (p.x - self._last_pos[0]) / dt
            pvy = (p.y - self._last_pos[1]) / dt
        self._last_pos = (p.x, p.y)
        self.player_speed = math.hypot(pvx, pvy)
        if self.player_speed > 1500:            # teleport / respawn spike
            self.player_speed = pvx = pvy = 0.0

        self.facing_hist.append((self.clock, math.atan2(p.facing.y, p.facing.x)))
        if p._dash_timer > 0:
            self.last_dash_active = self.clock

        area = g.current_area
        new_pos, vel = {}, {}
        nearest, nearest_d = None, 350.0
        for e in chain(area.enemies, area.bosses):
            if not e.alive or abs(e.x - p.x) > 450 or abs(e.y - p.y) > 450:
                continue
            k = id(e)
            new_pos[k] = (e.x, e.y)
            prev = self._prev_pos.get(k)
            if prev:
                vel[k] = ((e.x - prev[0]) / dt, (e.y - prev[1]) / dt)
            d = math.hypot(e.x - p.x, e.y - p.y)
            if d < nearest_d:
                nearest, nearest_d = e, d
        self._prev_pos, self.enemy_vel = new_pos, vel

        # Retreat: moving away from the nearest close enemy.
        if nearest is not None and nearest_d > 0:
            ax, ay = p.x - nearest.x, p.y - nearest.y
            al = math.hypot(ax, ay) or 1.0
            if (pvx * ax + pvy * ay) / al > RETREAT_SPEED_MIN:
                self.last_retreat = self.clock

    # ---------------- classification ----------------
    @staticmethod
    def _subtype(player):
        w = player.equipped.get("weapon")
        return getattr(w, "weapon_subtype", None) if w else None

    def _turned(self, player):
        cur = math.atan2(player.facing.y, player.facing.x)
        for t, a in self.facing_hist:
            if self.clock - t > REVERSE_WINDOW:
                continue
            diff = abs((cur - a + math.pi) % (2 * math.pi) - math.pi)
            if diff >= REVERSE_ANGLE:
                return True
        return False

    def classify_melee(self, player, profile):
        """Swing-level technique (decided before targets are gathered)."""
        cands = []
        if (self._subtype(player) == "greatsword"
                and self.clock - self.last_near_miss <= NEAR_MISS_WINDOW):
            cands.append("heavy_reversal")
        in_final_dash = 0 < player._dash_timer <= 0.09
        just_dashed = (player._dash_timer <= 0
                       and self.clock - self.last_dash_active <= DASH_GRACE)
        if in_final_dash or just_dashed:
            cands.append("dash_strike")
        if self._turned(player):
            cands.append("reverse_slash")
        for t in cands:
            if self.clock >= self._cd.get(t, 0.0):
                return t
        return None

    def widen(self, profile):
        return replace(profile, arc_degrees=min(300.0, profile.arc_degrees + 70.0),
                       range=profile.range * 1.1)

    def is_intercept(self, target):
        v = self.enemy_vel.get(id(target))
        if not v:
            return False
        p = self.game.player
        dx, dy = p.x - target.x, p.y - target.y
        d = math.hypot(dx, dy)
        if d < 1.0:
            return False
        if (v[0] * dx + v[1] * dy) / d < INTERCEPT_SPEED_MIN:
            return False
        # target must be in front of the player (within ~60 degrees)
        return (p.facing.x * -dx + p.facing.y * -dy) / d > 0.5

    def classify_ranged(self, player):
        if self.clock - self.last_retreat <= RETREAT_WINDOW:
            if self.clock >= self._cd.get("retreat_shot", 0.0):
                return "retreat_shot"
        return None

    def projectile_techniques(self, proj):
        techs = []
        if getattr(proj, "technique", None):
            techs.append(proj.technique)
        traveled = proj.max_range - proj.remaining_range
        if proj.max_range > 0 and traveled >= proj.max_range * PERFECT_RANGE_FRAC:
            techs.append("perfect_range")
        return techs

    # ---------------- application ----------------
    def trigger(self, techs, target):
        g = self.game
        labels = []
        for t in techs:
            label, _ = TECHNIQUES[t]
            labels.append(label)
            self._cd[t] = self.clock + TECH_COOLDOWN
            self.stats[t] += 1
            self._effect(t, target)
            g.event_bus.emit("kinetic_technique", name=t)
            if t not in self.discovered:
                self.discovered.add(t)
                g._show_message(f"Technique discovered: {label}!", duration=3.0)
        color = TECHNIQUES[techs[0]][1]
        g.damage_numbers.spawn(target.x, target.y - 58, " + ".join(labels), color)

    def _effect(self, t, target):
        if not target.alive:
            return
        if t == "reverse_slash" and hasattr(target, "apply_stagger"):
            target.apply_stagger(0.5)
        elif t == "intercept" and hasattr(target, "interrupt"):
            target.interrupt()
        elif t == "retreat_shot" and hasattr(target, "apply_slow"):
            target.apply_slow(2.5, 0.5)
        elif t == "heavy_reversal":
            self.game.reactions.expose(target, 4.0)
        # dash_strike (pierce) and perfect_range (bonus pierce) are
        # resolved in the damage call / projectile loop respectively.