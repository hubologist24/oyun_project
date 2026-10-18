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
    enables_blink: bool = False
    telegraph_time_blink: float = 0.55 
    blink_arrival_damage_ratio: float = 0.0


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
    summoner: bool = False

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

        self.summon_requests = 0
        self._summon_timer = 5.0

        self.blink_trails = []
        self.phase_just_changed = False

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

    def interrupt(self):
        """Intercept: cancels a telegraphed attack."""
        if self._state == "telegraph":
            self._state = "recover"
            self._state_timer = 0.7

    def _hit_player(self, player, dmg, event_bus):
        if player.take_damage(dmg, cause=self.damage_type):
            event_bus.emit("player_hit", source=self.name, damage=dmg,
                        damage_type=self.damage_type)

    def _update_phase(self):
        ratio = self.hp / self.max_hp if self.max_hp else 0
        best_index = 0
        for i, phase in enumerate(self.template.phases):
            if ratio <= phase.hp_threshold:
                best_index = i
        #self.phase_index = best_index
        #if best_index > self.phase_index and self.template.summoner:
        #    self.summon_requests += 3          # burst on phase change
        if best_index > self.phase_index:
            self.summon_requests += (5 if self.template.summoner else 2)
            self.phase_just_changed = True

        self.phase_index = best_index

    def player_armor_effectiveness(self) -> float:
        return self.current_phase.armor_effectiveness

    def update(self, dt, player, event_bus, world_rules=None):
    
        if not self.alive:
            return

        for trail in self.blink_trails:
                trail["life"] -= dt
        self.blink_trails = [t for t in self.blink_trails if t["life"] > 0]

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
                self._hit_player(player, dmg, event_bus)
                #player.take_damage(dmg,cause=self.damage_type)                
                self.projectiles.remove(proj)
                continue
            if proj["life"] <= 0:
                self.projectiles.remove(proj)

        dx = player.x - self.x
        dy = player.y - self.y
        dist = math.hypot(dx, dy)
        if dist > 0:
            self._facing = pygame.Vector2(dx / dist, dy / dist)

        # All bosses can call adds; summoner archetypes do so more aggressively.
        if dist < 600:
            self._summon_timer -= dt
            if self._summon_timer <= 0:
                base = 4 if self.template.summoner else 2
                self.summon_requests += base + self.phase_index
                self._summon_timer = 6.0 if self.template.summoner else 11.0
       

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
        from core import audio
        if phase.enables_blink and random.random() < 0.28:
            self._state = "telegraph"
            self._pending_action = "blink"
            self._state_timer = phase.telegraph_time_blink
            audio.play("telegraph_dash")   # same low ominous cue
        elif phase.enables_dash and dist_to_player > 90 and random.random() < 0.45:
            self._state = "telegraph"
            self._pending_action = "dash"
            self._state_timer = phase.telegraph_time_dash
            audio.play("telegraph_dash")
        elif dist_to_player > 150:
            self._state = "telegraph"
            self._pending_action = "ranged"
            self._state_timer = 0.45
            audio.play("telegraph_ranged")
        else:
            self._state = "telegraph"
            self._pending_action = "melee"
            self._state_timer = phase.telegraph_time_melee
            audio.play("telegraph_melee")

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
                self._hit_player(player, dmg, event_bus)
                #player.take_damage(dmg,cause=self.damage_type)
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
                self._hit_player(player, dmg, event_bus)
                #player.take_damage(dmg,cause=self.damage_type)
            if phase.enrage_vulnerability_after_dash > 0:
                self.enraged_vulnerable_timer = phase.enrage_vulnerability_after_dash
        elif action == "blink":
            origin_x, origin_y = self.x, self.y

            # Random landing spot around the player -- can be behind them.
            angle = random.uniform(0, 2 * math.pi)
            dist = random.uniform(110, 240)
            self.x = player.x + math.cos(angle) * dist
            self.y = player.y + math.sin(angle) * dist

            dx = player.x - self.x
            dy = player.y - self.y
            d = math.hypot(dx, dy) or 1.0
            self._facing = pygame.Vector2(dx / d, dy / d)

            # --- record the trail so the player can track the leap ---
            self.blink_trails.append({
                "x0": origin_x, "y0": origin_y,
                "x1": self.x, "y1": self.y,
                "life": 0.35, "max_life": 0.35,
            })

            # --- optional arrival shockwave ---
            ratio = getattr(phase, "blink_arrival_damage_ratio", 0.0)
            if ratio > 0 and math.hypot(player.x - self.x, player.y - self.y) <= 110:
                burst = int(self.template.melee_damage * ratio)
                dmg = resolve_damage(burst, self.damage_type, None,
                                    target_resistances=target_resist,
                                    world_rules=world_rules,
                                    target_category="player")
                self._hit_player(player, dmg, event_bus)

            self._state = "recover"
            self._state_timer = 0.30
            return

        self._state = "recover"
        self._state_timer = 0.4

    def draw(self, surface, camera):
        import core.config as config

    # Blink trails first so the boss renders on top of its own wake.
        for trail in self.blink_trails:
            frac = trail["life"] / trail["max_life"]
            p0 = camera.world_to_screen((trail["x0"], trail["y0"]))
            p1 = camera.world_to_screen((trail["x1"], trail["y1"]))
            # Outer dark-purple glow, then a bright inner core line.
            outer = (int(120 * frac), int(60 * frac), int(180 * frac))
            inner = (int(220 * frac), int(160 * frac), int(255 * frac))
            pygame.draw.line(surface, outer, p0, p1, 7)
            pygame.draw.line(surface, inner, p0, p1, 2)

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