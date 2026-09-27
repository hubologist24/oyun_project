"""
Generic data-driven boss (Phase 3).

HollowWarden (Phase 1/2) is now expressed as a BossTemplate instance
rather than a hardcoded class -- this is the seam that lets extensions
(and eventually AI world specs) define new bosses purely through data,
validated by BossValidator (Phase 5+), while the actual combat logic
(phases, telegraphs, dash, vulnerability windows) lives once in this
engine class.
"""
import pygame
import math
import random
from dataclasses import dataclass, field
from typing import List, Dict
from combat.damage import resolve_damage


@dataclass
class BossPhaseConfig:
    hp_threshold: float          # phase activates when hp_ratio <= this value (1.0 = always active)
    enables_dash: bool = False
    armor_effectiveness: float = 1.0     # multiplier applied to player's armor value
    telegraph_time_melee: float = 0.6
    telegraph_time_dash: float = 0.5
    attack_cooldown: float = 1.0
    enrage_vulnerability_after_dash: float = 0.0  # seconds of 2x damage taken after dash


@dataclass
class BossTemplate:
    name: str
    max_hp: int
    armor: int = 25
    melee_damage: int = 34
    ranged_damage: int = 22
    dash_damage: int = 46
    damage_type: str = "physical"
    resistances: Dict[str, float] = field(default_factory=dict)
    phases: List[BossPhaseConfig] = field(default_factory=list)

    @staticmethod
    def hollow_warden():
        """The original Phase 1 starting boss, now expressed as data."""
        return BossTemplate(
            name="The Hollow Warden",
            max_hp=1400,
            armor=25,
            melee_damage=34,
            ranged_damage=22,
            dash_damage=46,
            damage_type="physical",
            resistances={},
            phases=[
                BossPhaseConfig(hp_threshold=1.0, enables_dash=False, armor_effectiveness=1.0,
                                 attack_cooldown=1.2),
                BossPhaseConfig(hp_threshold=0.6, enables_dash=True, armor_effectiveness=0.55,
                                 attack_cooldown=0.9),
                BossPhaseConfig(hp_threshold=0.25, enables_dash=True, armor_effectiveness=0.55,
                                 attack_cooldown=0.6, enrage_vulnerability_after_dash=1.5,
                                 telegraph_time_melee=0.4, telegraph_time_dash=0.32),
            ],
        )


class Boss:
    """Runtime boss instance driven by a BossTemplate."""

    def __init__(self, x, y, template: BossTemplate):
        self.x = x
        self.y = y
        self.width = 64
        self.height = 64
        self.template = template
        self.name = template.name
        self.is_boss = True

        self.max_hp = template.max_hp
        self.hp = self.max_hp
        self.armor = template.armor
        self.damage_type = template.damage_type
        self.resistances = dict(template.resistances)
        self.resistances.setdefault("armor_flat", template.armor)

        self.phase_index = 0
        self.alive = True
        self.enraged_vulnerable_timer = 0.0

        self._state = "idle"
        self._state_timer = 0.0
        self._cooldown_timer = 1.0

        self._dash_target = pygame.Vector2(x, y)
        self._facing = pygame.Vector2(0, 1)
        self.projectiles = []

    @property
    def rect(self) -> pygame.Rect:
        return pygame.Rect(int(self.x - self.width / 2), int(self.y - self.height / 2),
                            self.width, self.height)

    @property
    def current_phase(self) -> BossPhaseConfig:
        return self.template.phases[self.phase_index]

    def take_damage(self, amount: int):
        multiplier = 2.0 if self.enraged_vulnerable_timer > 0 else 1.0
        self.hp -= int(amount * multiplier)
        if self.hp <= 0:
            self.hp = 0
            self.alive = False
        self._update_phase()

    def _update_phase(self):
        ratio = self.hp / self.max_hp if self.max_hp else 0
        best_index = 0
        for i, phase in enumerate(self.template.phases):
            if ratio <= phase.hp_threshold:
                best_index = i
        self.phase_index = best_index

    def player_armor_effectiveness(self) -> float:
        return self.current_phase.armor_effectiveness

    def update(self, dt, player, event_bus, world_rules=None):
        if not self.alive:
            return

        if self.enraged_vulnerable_timer > 0:
            self.enraged_vulnerable_timer -= dt

        for proj in list(self.projectiles):
            proj["x"] += proj["vx"] * dt
            proj["y"] += proj["vy"] * dt
            proj["life"] -= dt
            dist = math.hypot(proj["x"] - player.x, proj["y"] - player.y)
            if dist < 22:
                effective_armor = player.effective_stats.armor * self.player_armor_effectiveness()
                dmg = resolve_damage(proj["damage"], self.damage_type, None,
                                      target_resistances={"armor_flat": effective_armor,
                                                           **player.effective_stats.resistances},
                                      world_rules=world_rules, target_category="player")
                player.take_damage(dmg,cause=self.damage_type)                
                self.projectiles.remove(proj)
                continue
            if proj["life"] <= 0:
                self.projectiles.remove(proj)

        dx = player.x - self.x
        dy = player.y - self.y
        dist = math.hypot(dx, dy)
        if dist > 0:
            self._facing = pygame.Vector2(dx / dist, dy / dist)

        if self._cooldown_timer > 0:
            self._cooldown_timer -= dt

        phase = self.current_phase

        if self._state == "idle":
            if self._cooldown_timer <= 0 and dist < 420:
                self._choose_action(dist, phase)
        elif self._state == "telegraph":
            self._state_timer -= dt
            if self._state_timer <= 0:
                self._execute_attack(player, event_bus, phase, world_rules)
        elif self._state == "recover":
            self._state_timer -= dt
            if self._state_timer <= 0:
                self._state = "idle"
                self._cooldown_timer = phase.attack_cooldown

    def _choose_action(self, dist_to_player, phase: BossPhaseConfig):
        if phase.enables_dash and dist_to_player > 90 and random.random() < 0.45:
            self._state = "telegraph"
            self._pending_action = "dash"
            self._state_timer = phase.telegraph_time_dash
        elif dist_to_player > 150:
            self._state = "telegraph"
            self._pending_action = "ranged"
            self._state_timer = 0.45
        else:
            self._state = "telegraph"
            self._pending_action = "melee"
            self._state_timer = phase.telegraph_time_melee

    def _execute_attack(self, player, event_bus, phase: BossPhaseConfig, world_rules):
        action = getattr(self, "_pending_action", "melee")
        effective_armor = player.effective_stats.armor * self.player_armor_effectiveness()
        target_resist = dict(player.effective_stats.resistances, armor_flat=effective_armor)

        if action == "melee":
            dist = math.hypot(player.x - self.x, player.y - self.y)
            if dist < 90:
                dmg = resolve_damage(self.template.melee_damage, self.damage_type, None,
                                      target_resistances=target_resist, world_rules=world_rules,
                                      target_category="player")
                player.take_damage(dmg,cause=self.damage_type)
        elif action == "ranged":
            self.projectiles.append({
                "x": self.x, "y": self.y,
                "vx": self._facing.x * 320, "vy": self._facing.y * 320,
                "damage": self.template.ranged_damage, "life": 2.5,
            })
        elif action == "dash":
            self.x += self._facing.x * 140
            self.y += self._facing.y * 140
            dist = math.hypot(player.x - self.x, player.y - self.y)
            if dist < 100:
                dmg = resolve_damage(self.template.dash_damage, self.damage_type, None,
                                      target_resistances=target_resist, world_rules=world_rules,
                                      target_category="player")
                player.take_damage(dmg,cause=self.damage_type)
            if phase.enrage_vulnerability_after_dash > 0:
                self.enraged_vulnerable_timer = phase.enrage_vulnerability_after_dash

        self._state = "recover"
        self._state_timer = 0.4

    def draw(self, surface, camera):
        import core.config as config
        rect = camera.world_to_screen_rect(self.rect)
        color = config.COLOR_BOSS
        if self._state == "telegraph":
            t = pygame.time.get_ticks() % 200
            color = (255, 255, 255) if t < 100 else config.COLOR_BOSS
        if self.enraged_vulnerable_timer > 0:
            color = (255, 215, 0)
        pygame.draw.rect(surface, color, rect, border_radius=10)

        for proj in self.projectiles:
            p = camera.world_to_screen((proj["x"], proj["y"]))
            pygame.draw.circle(surface, (255, 120, 40), p, 6)

        font = pygame.font.SysFont("consolas", 14)
        label = f"{self.name}  (Phase {self.phase_index + 1})"
        text = font.render(label, True, config.COLOR_TEXT)
        surface.blit(text, (rect.centerx - text.get_width() // 2, rect.y - 26))


# Backward-compat alias so existing imports (`from enemies.boss import HollowWarden`) still work
def HollowWarden(x, y):
    return Boss(x, y, BossTemplate.hollow_warden())