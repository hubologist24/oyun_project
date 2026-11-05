import core.config as config
from core import audio

SLOWMO_DURATION = 0.35
SLOWMO_TIMESCALE = 0.25
CRIT_WINDOW = 1.5
PERFECT_DODGE_COOLDOWN = 1.2
COUNTER_DAMAGE_MULT = 1.5


class PerfectDodgeMixin:

    def _init_perfect_dodge(self):
        self._slowmo_timer = 0.0
        self._pd_cooldown = 0.0
        self._pd_counter_active = False
        self._pd_counter_timer = 0.0
        self.event_bus.subscribe("near_miss", self._on_perfect_dodge_near_miss)

    def _on_perfect_dodge_near_miss(self, **kw):
        if self._pd_cooldown > 0:
            return
        self._pd_cooldown = PERFECT_DODGE_COOLDOWN
        self._slowmo_timer = SLOWMO_DURATION
        self.player.guaranteed_crit_timer = CRIT_WINDOW
        self._pd_counter_active = True
        self._pd_counter_timer = CRIT_WINDOW

        self.crit_bursts.append({
            "x": self.player.x, "y": self.player.y, "radius": 46,
            "life": 0.25, "max_life": 0.25,
            "color": (255, 255, 255),
        })
        self.damage_numbers.spawn(self.player.x, self.player.y - 44,
                                  "PERFECT DODGE", (180, 240, 255))
        self.camera.shake(magnitude=2, duration=0.15)
        audio.play("hit_enemy")

    def _consume_perfect_dodge_counter(self) -> bool:
        """True iff the next player attack should receive the counter bonus."""
        if not self._pd_counter_active:
            return False
        self._pd_counter_active = False
        self._pd_counter_timer = 0.0
        return True

    def _current_timescale(self):
        if self._slowmo_timer > 0:
            return SLOWMO_TIMESCALE
        return 1.0

    def _tick_perfect_dodge(self, real_dt):
        if self._slowmo_timer > 0:
            self._slowmo_timer -= real_dt
        if self._pd_cooldown > 0:
            self._pd_cooldown -= real_dt
        if self._pd_counter_active:
            self._pd_counter_timer -= real_dt
            if self._pd_counter_timer <= 0:
                self._pd_counter_active = False