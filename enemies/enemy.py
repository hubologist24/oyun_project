"""
Base enemy - Phase 2: adds a resistances dict (defaults empty = pure
armor-based mitigation, same as Phase 1 behavior) so future world
rules / enemy types can define per-type resistance profiles.
"""
import pygame
import random
import core.config as config
import math

from enemies.ai_intelligence import (
    PlayerObserver, intel_tier_for, attack_jitter,
    flank_offset_point, wants_retreat,
    _enabled as _ai_enabled,
)


from core.difficulty import D, area_difficulty_mult

ELITE_AFFIXES = ["swift", "armored", "explosive", "regenerating",
                 "thorns", "vampiric", "shielded", "teleporting", "frost_nova"]

AFFIX_COLORS = {
    "swift":        (90, 220, 255),
    "armored":      (190, 190, 200),
    "explosive":    (255, 140, 40),
    "regenerating": (110, 255, 130),
    "thorns":       (255, 100, 100),
    "vampiric":     (200, 60, 120),
    "shielded":     (110, 180, 255),
    "teleporting":  (200, 100, 255),
    "frost_nova":   (150, 220, 255),
    "overcharged":  (200, 100, 255),
}

class Enemy:
    def __init__(self, x, y, name="Enemy", hp=30, damage=8, armor=0,
                 speed=90.0, xp_reward=15, is_elite=False, area_level=1,
                 damage_type="physical", resistances=None  ,behavior="chaser"):
        self.x = x
        self.y = y
        self.width = 28
        self.height = 28
        self.name = name
        self.max_hp = hp
        self.hp = hp
        self.damage = damage
        self.armor = armor
        self.speed = speed
        self.xp_reward = xp_reward
        self.is_elite = is_elite
        self.area_level = area_level
        self.damage_type = damage_type
        self.resistances = dict(resistances) if resistances else {}
        self.resistances.setdefault("armor_flat", armor)
        self.affixes = []

        self.behavior = behavior

        self.personality = "brave"
        self.personality_damage_mult = 1.0
        self._flee_flag = False

        self._strafe_dir = 1 if random.random() < 0.5 else -1
        self._strafe_timer = 0.0
        self._ranged_timer = 1.2
        self.projectiles = []

        self.stagger_timer = 0.0

        self.posture = 0.0
        self.posture_max = 100.0
        self.broken_timer = 0.0
        self.just_broke = False

        self.slow_timer = 0.0
        self.slow_factor = 1.0

        self.pack = None
        self.pack_role = None
        self.pack_speed_mult = 1.0
        self.pack_damage_mult = 1.0
        self._pack_checked = False

        self.alive = True
        self._attack_timer = 0.0
        self._aggro_range = 260

        self.hit_flash_timer = 0.0

        self.pack_commander_shield = 0.0    # NEW
        self.pack_commander_armor_bonus = 0

        # Affix runtime state
        self.shield_hp = 0
        self._teleport_cd = random.uniform(3.0, 6.0)
        self._frost_nova_cd = random.uniform(4.0, 8.0)

        # Telegraph state (brute slam / frost nova)
        self._telegraph_timer = 0.0
        self._time_since_damage = 999.0
        

        self._telegraph_pos = None
        self._telegraph_action = None

        # --- AI intelligence state ---
        self._observer = PlayerObserver()
        self._intel_tier = intel_tier_for(behavior, is_elite)
        self._flank_side = 1 if random.random() < 0.5 else -1
        self._retreat_mode = False
        self._retreat_reengage = 0.0

        self.is_spawner = False
        self.is_convoy_carrier = False
        self._escape_checked = False

    _affix_font = None

    @property
    def rect(self) -> pygame.Rect:
        return pygame.Rect(int(self.x - self.width / 2), int(self.y - self.height / 2),
                            self.width, self.height)

    def take_damage(self, amount: int,source=None):
        self._time_since_damage = 0.0

        if self.broken_timer > 0:
            amount = int(amount * 1.5)
        if self.stagger_timer > 0:
            self.apply_posture(15)

        if self.pack is not None:
            self.pack.alerted = True

        # Shield absorbs first
        if self.shield_hp > 0 and amount > 0:
            absorbed = min(self.shield_hp, amount)
            self.shield_hp -= absorbed
            amount -= absorbed

        # NEW: commander aura damage reduction
        if self.pack_commander_shield > 0 and amount > 0:
            amount = max(1, int(amount * (1.0 - self.pack_commander_shield)))

        if self.pack_commander_armor_bonus > 0 and amount > 0:
             amount = max(1, amount - self.pack_commander_armor_bonus)

         # Thorns: reflect a fraction of melee damage back to the attacker
        if amount > 0 and "thorns" in self.affixes and source is not None:
            reflect = max(1, int(amount * 0.25))
            try:
                if source.take_damage(reflect, cause=self.damage_type):
                    self.game_ref_damage_numbers(source.x, source.y, reflect) if False else None
            except Exception:
                pass

        self.hp -= amount
        self.hit_flash_timer = 0.15
        if self.hp <= 0:
            self.hp = 0
            self.alive = False


    def apply_stagger(self, duration):
        self.stagger_timer = max(self.stagger_timer, duration)
        self.apply_posture(25)


    def apply_posture(self, amount):
        if self.broken_timer > 0:
            return
        self.posture += amount
        if self.posture >= self.posture_max:
            self.posture = 0.0
            self.broken_timer = 2.5
            self.just_broke = True
            self.apply_slow(2.5, 0.5)

    def apply_slow(self, duration, factor=0.5):
        self.slow_timer = max(self.slow_timer, duration)
        self.slow_factor = factor

    def interrupt(self):
        self._attack_timer = max(self._attack_timer, 1.0)
        self._ranged_timer = max(self._ranged_timer, 1.5)
        self.apply_stagger(0.3)
        self.apply_posture(20)       

    def update(self, dt, player, tilemap, event_bus, world_rules=None):
        self._world_rules = world_rules

        if _ai_enabled():
            self._observer.update(dt, player)

        if self.hit_flash_timer > 0:
            self.hit_flash_timer -= dt

        if self.broken_timer > 0:
            self.broken_timer -= dt

        if not self.alive:
            return

        self._time_since_damage += dt
        if (self.hp < self.max_hp
                and self._time_since_damage > 5.0
                and dist_from_player_placeholder if False else True):
            pass  # (see below – real check next to `dist` calc)

        if self.personality == "berserker":
            self.personality_damage_mult = (
                1.4 if self.hp < self.max_hp * 0.5 else 1.0)
        elif self.personality == "opportunist":
            hp_frac = (player.effective_stats.hp
                    / max(1, player.effective_stats.max_hp))
            self.personality_damage_mult = 1.3 if hp_frac < 0.5 else 1.0
        elif self.personality == "coward" and player.alive:
            if self.hp < self.max_hp * 0.3:
                has_commander = (
                    self.pack is not None
                    and any(m.alive and m.pack_role == "commander"
                            for m in self.pack.members))
                if not has_commander:
                    self._flee_flag = True
                    return


        if (self.is_elite
                and not getattr(self, "nemesis", None)
                and not getattr(self, "_escape_checked", False)
                and self.hp < self.max_hp * 0.15
                and self.hp > 0):
            self._escape_checked = True
            if random.random() < 0.25:
                self._flee_flag = True
                return

        if "regenerating" in self.affixes and self.hp < self.max_hp:
            self.hp = min(self.max_hp, self.hp + self.max_hp * 0.04 * dt)

        # --- Compute distance vectors early for affixes ---
        dx = player.x - self.x
        dy = player.y - self.y
        dist = math.hypot(dx, dy)
        if self.hp < self.max_hp and self._time_since_damage > 5.0 and dist > 400:
            self.hp = min(self.max_hp, self.hp + self.max_hp * 0.03 * dt)

        # --- telegraph executes when the timer expires ---
        if self._telegraph_timer > 0:
            self._telegraph_timer -= dt
            if self._telegraph_timer <= 0:
                self._execute_telegraph(player, event_bus)
                return

        # --- teleporting affix ---
        if "teleporting" in self.affixes:
            self._teleport_cd -= dt
            if self._teleport_cd <= 0 and dist < 500:
                self._teleport_cd = random.uniform(4.0, 7.0)
                angle = random.uniform(0, 2 * math.pi)
                nx = player.x + math.cos(angle) * 110
                ny = player.y + math.sin(angle) * 110
                if not tilemap.collides_rect(self._rect_at(nx, ny)):
                    self.x, self.y = nx, ny
                    self._attack_timer = max(self._attack_timer, 0.4)
                    # Recalculate distance after teleporting so subsequent checks use valid data
                    dx = player.x - self.x
                    dy = player.y - self.y
                    dist = math.hypot(dx, dy)

        # --- frost nova affix ---
        if "frost_nova" in self.affixes and self._telegraph_timer <= 0:
            self._frost_nova_cd -= dt
            if self._frost_nova_cd <= 0 and dist < 260:
                self._frost_nova_cd = random.uniform(5.0, 8.0)
                self._start_telegraph("frost_nova", player)    

        if self.slow_timer > 0:
            self.slow_timer -= dt
        if self.stagger_timer > 0:
            self.stagger_timer -= dt
            self._tick_projectiles(dt, player, event_bus)
            return

        if self._attack_timer > 0:
            self._attack_timer -= dt
        if self._ranged_timer > 0:
            self._ranged_timer -= dt
        self._strafe_timer -= dt
        if self._strafe_timer <= 0:
            self._strafe_dir *= -1
            self._strafe_timer = random.uniform(0.8, 2.2)

        # Recalculate in case timers/movement shifted positions further before behaviors run
        dx = player.x - self.x
        dy = player.y - self.y
        dist = math.hypot(dx, dy)

        if self.pack is not None and self.pack_role:
            if self.pack.handle(self, dt, player, tilemap, dist):
                self._tick_projectiles(dt, player, event_bus)
                return

        handler = {
            "chaser":   self._tick_chaser,
            "ambusher": self._tick_ambusher,
            "circler":  self._tick_circler,
            "ranged":   self._tick_ranged,
            "brute":    self._tick_chaser,
            "swarmer":  self._tick_chaser,
        }.get(self.behavior, self._tick_chaser)
        
        handler(dt, player, tilemap, dist, dx, dy, event_bus)
        self._tick_projectiles(dt, player, event_bus)

    def _rect_at(self, x, y):
        return pygame.Rect(int(x - self.width / 2), int(y - self.height / 2),
                            self.width, self.height)


    def _contact_attack(self, player, event_bus):
        if self._attack_timer > 0 or self._telegraph_timer > 0:
            return
        if self.behavior == "brute":
            self._start_telegraph("slam", player)
            return
        from combat.combat import enemy_attack_player
        enemy_attack_player(self, player, event_bus, world_rules=self._world_rules)
        self._attack_timer = (config.ENEMY_CONTACT_DAMAGE_COOLDOWN
                              * D.get("enemy_attack_cooldown_mult")
                              * attack_jitter())
        if "vampiric" in self.affixes:
            self.hp = min(self.max_hp, self.hp + int(self.damage * 0.5))

    def _start_telegraph(self, action, player):
        self._telegraph_action = action
        self._telegraph_timer = 0.6 if action == "slam" else 0.75
        self._telegraph_pos = (player.x, player.y)

    def _execute_telegraph(self, player, event_bus):
        action = self._telegraph_action
        pos = self._telegraph_pos
        self._telegraph_action = None
        self._telegraph_pos = None
        if pos is None:
            return
        from combat.combat import enemy_attack_player
        if action == "slam":
            radius = 70
            if math.hypot(player.x - pos[0], player.y - pos[1]) <= radius:
                enemy_attack_player(self, player, event_bus,
                                    world_rules=self._world_rules,
                                    damage_multiplier=1.4)
        elif action == "frost_nova":
            radius = 85
            for target in list(self.game_current_targets_of_opportunity()):
                if math.hypot(target.x - pos[0], target.y - pos[1]) <= radius:
                    target.take_damage(max(1, int(self.damage * 0.8)))
            if math.hypot(player.x - pos[0], player.y - pos[1]) <= radius:
                enemy_attack_player(self, player, event_bus,
                                    world_rules=self._world_rules)
        
        self._attack_timer = (config.ENEMY_CONTACT_DAMAGE_COOLDOWN
                              * D.get("enemy_attack_cooldown_mult")
                              * attack_jitter())

    def game_current_targets_of_opportunity(self):
        # No direct game ref — return an empty iterable. Enemies in a pack
        # still take the player-facing nova; the enemy-vs-enemy AoE is
        # intentionally avoided (design choice: keep elites threatening
        # to the player, not each other).
        return ()

    def draw(self, surface, camera):
        rect = camera.world_to_screen_rect(self.rect)

        if getattr(self, "is_spawner", False):
            pygame.draw.rect(surface, (140, 30, 30), rect, border_radius=4)
            pygame.draw.rect(surface, (255, 100, 100), rect, 2, border_radius=4)
            if self.hp < self.max_hp:
                frac = max(0.0, self.hp / self.max_hp)
                bar_w, bar_h = self.width + 8, 4
                bx, by = rect.x - 4, rect.y - 10
                pygame.draw.rect(surface, (40, 10, 10),
                                 (bx, by, bar_w, bar_h))
                pygame.draw.rect(surface, (230, 60, 60),
                                 (bx, by, int(bar_w * frac), bar_h))
            return

        base = config.COLOR_ELITE if self.is_elite else config.COLOR_ENEMY
        tint = {"chaser": (0, 0, 0), "ambusher": (40, 0, 40),
                "circler": (0, 30, 40), "ranged": (40, 20, 0),
                "brute": (0, 0, 70), "swarmer": (0, 60, 0)}.get(self.behavior, (0, 0, 0))
        color = tuple(min(255, c + t) for c, t in zip(base, tint))
        if self.hit_flash_timer > 0:
            color = (255, 255, 255)
        pygame.draw.rect(surface, color, rect, border_radius=4)


        if self.hp < self.max_hp:
            bar_w, bar_h = self.width, 5
            bx, by = rect.x, rect.y - 10
            pygame.draw.rect(surface, config.COLOR_HP_BAR_BG, (bx, by, bar_w, bar_h))
            frac = max(0.0, self.hp / self.max_hp)
            pygame.draw.rect(surface, config.COLOR_HP_BAR_FG,
                            (bx, by, int(bar_w * frac), bar_h))

        if self.posture > 0 or self.broken_timer > 0:
            bar_w, bar_h = self.width, 3
            bx, by = rect.x, rect.y - 16
            pygame.draw.rect(surface, (40, 20, 10), (bx, by, bar_w, bar_h))
            frac = 1.0 if self.broken_timer > 0 else self.posture / self.posture_max
            color = (255, 200, 60) if self.broken_timer > 0 else (200, 140, 60)
            pygame.draw.rect(surface, color, (bx, by, int(bar_w * frac), bar_h))

        for proj in self.projectiles:
            p = camera.world_to_screen((proj["x"], proj["y"]))
            pygame.draw.circle(surface, (255, 160, 80), p, 5)
            pygame.draw.circle(surface, (255, 255, 255), p, 5, 1)

        if self.affixes:
            if Enemy._affix_font is None:
                Enemy._affix_font = pygame.font.SysFont("consolas", 11, bold=True)
            col = AFFIX_COLORS[self.affixes[0]]
            pygame.draw.rect(surface, col, rect, 2, border_radius=4)
            t = Enemy._affix_font.render("/".join(self.affixes), True, col)
            surface.blit(t, (rect.centerx - t.get_width() // 2, rect.y - 22))

        if self.stagger_timer > 0:
            pygame.draw.rect(surface, (255, 255, 255), rect.inflate(6, 6), 2, border_radius=5)
        elif self.slow_timer > 0:
            pygame.draw.rect(surface, (120, 220, 255), rect.inflate(6, 6), 1, border_radius=5)

        if self._telegraph_timer > 0 and self._telegraph_pos is not None:
            color = (255, 100, 40) if self._telegraph_action == "slam" else (150, 220, 255)
            radius = 70 if self._telegraph_action == "slam" else 85
            p = camera.world_to_screen(self._telegraph_pos)
            total = 0.6 if self._telegraph_action == "slam" else 0.75
            frac = 1.0 - max(0.0, self._telegraph_timer) / total
            pygame.draw.circle(surface, color, p, radius, 2)
            pygame.draw.circle(surface, color, p, max(2, int(radius * frac)), 1)

    def _step_toward(self, dt, tilemap, dir_x, dir_y, speed_scale=1.0):
        v = pygame.Vector2(dir_x, dir_y)
        if v.length_squared() == 0:
            return
        v = v.normalize()
        if self.slow_timer > 0:
            speed_scale *= self.slow_factor
        speed_scale *= self.pack_speed_mult
        nx = self.x + v.x * self.speed * speed_scale * dt
        ny = self.y + v.y * self.speed * speed_scale * dt
     

        if not tilemap.collides_rect(self._rect_at(nx, self.y)):
            self.x = nx
        if not tilemap.collides_rect(self._rect_at(self.x, ny)):
            self.y = ny



        
    def _count_nearby_allies(self) -> int:
        if self.pack is None:
            return 0
        return sum(1 for m in self.pack.members
                   if m is not self and m.alive
                   and math.hypot(m.x - self.x, m.y - self.y) < 300.0)

    def _maybe_retreat(self, dt, player, tilemap, dist) -> bool:
        """If wounded with allies nearby, back away for a beat.  Returns True if
        the retreat path handled this frame (caller should return)."""
        if self._intel_tier < 2:
            return False
        if not wants_retreat(self.hp, self.max_hp, self._count_nearby_allies()):
            self._retreat_mode = False
            return False
        self._retreat_mode = True
        if dist < 20:
            return False
        # If very far, stop retreating and re-engage
        if dist > 420:
            self._retreat_mode = False
            return False
        away_x = self.x - player.x
        away_y = self.y - player.y
        self._step_toward(dt, tilemap, away_x, away_y, 1.3)
        return True


    def _tick_chaser(self, dt, player, tilemap, dist, dx, dy, event_bus):
        if self._maybe_retreat(dt, player, tilemap, dist):
            return
        if dist < self._aggro_range and dist > 34:
            # Smart enemies flank when they're mid-range
            if (self._intel_tier >= 2
                    and getattr(config, "AI_MOB_FLANKING", True)
                    and 100 < dist < 260):
                fx, fy = flank_offset_point(self.x, self.y,
                                             player.x, player.y,
                                             self._flank_side)
                self._step_toward(dt, tilemap, fx - self.x, fy - self.y, 1.0)
            else:
                self._step_toward(dt, tilemap, dx, dy, 1.0)
        elif dist <= 34:
            self._contact_attack(player, event_bus)

    def _tick_ambusher(self, dt, player, tilemap, dist, dx, dy, event_bus):
        if dist > 220:
            return
        # True ambush: burst in the punish window
        if (self._intel_tier >= 2 and self._observer.in_punish_window()
                and dist < 160):
            self._step_toward(dt, tilemap, dx, dy, 2.5)
            if dist <= 40:
                self._contact_attack(player, event_bus)
            return
        if dist > 60:
            self._step_toward(dt, tilemap, dx, dy, 0.4)
        elif dist > 34:
            self._step_toward(dt, tilemap, dx, dy, 2.2)
        else:
            self._contact_attack(player, event_bus)

    def _tick_circler(self, dt, player, tilemap, dist, dx, dy, event_bus):
        if dist > self._aggro_range:
            return
        if self._maybe_retreat(dt, player, tilemap, dist):
            return
        # Punish window: circle INWARD for a quick strike
        if (self._intel_tier >= 2 and self._observer.in_punish_window()
                and 40 < dist < 140):
            self._step_toward(dt, tilemap, dx, dy, 1.8)
            if dist <= 45:
                self._contact_attack(player, event_bus)
            return
        if dist > 160:
            self._step_toward(dt, tilemap, dx, dy, 1.0)
        elif dist < 90:
            self._step_toward(dt, tilemap, -dx, -dy, 0.9)
        else:
            perp_x, perp_y = -dy, dx
            self._step_toward(dt, tilemap, perp_x * self._strafe_dir,
                              perp_y * self._strafe_dir, 1.0)
        if dist <= 40:
            self._contact_attack(player, event_bus)

    def _tick_ranged(self, dt, player, tilemap, dist, dx, dy, event_bus):
        if dist > self._aggro_range + 60:
            return
        if self._maybe_retreat(dt, player, tilemap, dist):
            return
        if dist < 160:
            self._step_toward(dt, tilemap, -dx, -dy, 1.1)
        elif dist > 300:
            self._step_toward(dt, tilemap, dx, dy, 0.8)
        else:
            perp_x, perp_y = -dy, dx
            self._step_toward(dt, tilemap, perp_x * self._strafe_dir,
                              perp_y * self._strafe_dir, 0.9)
        if dist <= 380 and self._ranged_timer <= 0:
            if (self._intel_tier >= 2
                    and getattr(config, "AI_MOB_PREDICTIVE_AIM", True)):
                # Lead the shot: predict where the player will be when
                # the projectile arrives.
                lead = dist / 260.0
                px, py = self._observer.predict_player_position(player, lead)
                self._fire_bolt_at(px, py)
            else:
                self._fire_bolt(dx, dy, dist)
            self._ranged_timer = (1.6 * D.get("enemy_attack_cooldown_mult")
                                  * attack_jitter())

    def _fire_bolt_at(self, target_x, target_y):
        dx = target_x - self.x
        dy = target_y - self.y
        dist = math.hypot(dx, dy) or 1.0
        self._fire_bolt(dx, dy, dist)


    def _fire_bolt(self, dx, dy, dist):
        if dist < 1:
            return
        if self.pack is not None:
            self.pack.alerted = True
        speed = 260.0
        self.projectiles.append({
            "x": self.x, "y": self.y,
            "vx": dx / dist * speed,
            "vy": dy / dist * speed,
            "damage": int(self.damage * self.pack_damage_mult * self.personality_damage_mult),
            "life": 3.0,
        })


    def _tick_projectiles(self, dt, player, event_bus):
        from combat.damage import resolve_damage , level_damage_mult
        for proj in list(self.projectiles):
            proj["x"] += proj["vx"] * dt
            proj["y"] += proj["vy"] * dt
            proj["life"] -= dt
            if proj["life"] <= 0:
                self.projectiles.remove(proj)
                continue
            if math.hypot(proj["x"] - player.x, proj["y"] - player.y) < 18:
                stats = player.effective_stats
                target_res = dict(stats.resistances)
                target_res["armor_flat"] = stats.armor
                amount = proj["damage"] * level_damage_mult(player.progression.level, self.area_level)
                dmg = resolve_damage(amount, self.damage_type, None,
                                    target_resistances=target_res,
                                    target_category="player", world_rules=self._world_rules)
                #event_bus.emit("attacker_info", enemy=self)
                if player.take_damage(dmg, cause=self.damage_type):
                    event_bus.emit("attacker_info", enemy=self)
                    event_bus.emit("player_hit", source=self.name,
                                damage=dmg, damage_type=self.damage_type)
                self.projectiles.remove(proj)

BEHAVIOR_TABLE = [
    ("chaser",   50, 1.0, 1.0, 95.0),
    ("ambusher", 20, 0.9, 1.2, 110.0),
    ("circler",  20, 1.0, 0.9, 105.0),
    ("ranged",   10, 0.8, 0.8, 85.0),
    ("brute",     8, 2.5, 1.5, 65.0),    # slow tank, hits hard
    ("swarmer",  14, 0.45, 0.6, 135.0),
]

PERSONALITY_POOL = [
    ("brave", 50), ("coward", 20), ("berserker", 15),
    ("opportunist", 10), ("swift", 5),
]
PERSONALITY_PREFIX = {
    "brave": "", "coward": "Skittish ", "berserker": "Frenzied ",
    "opportunist": "Cunning ", "swift": "Swift ",
}


NAME_POOL = {
    "chaser":   ["Feral Rat", "Bandit", "Wild Wolf", "Skeleton"],
    "ambusher": ["Lurker", "Cave Spider", "Stalker"],
    "circler":  ["Wisp", "Dust Shade", "Slinker"],
    "ranged":   ["Cultist", "Bone Archer", "Hexer"],
    "brute":    ["Ogre", "Bone Golem", "Crypt Brute"],
    "swarmer":  ["Carrion Bat", "Gnawer", "Imp"]
}


#ELITE_AFFIXES = ["swift", "armored", "explosive", "regenerating"]
#AFFIX_COLORS = {"swift": (90, 220, 255), "armored": (190, 190, 200),
#                "explosive": (255, 140, 40), "regenerating": (110, 255, 130),
#                  "overcharged": (200, 100, 255),     # NEW
#                  }

def spawn_basic_enemy(x, y, rng, area_level=1, force_behavior=None,behavior_bias=None):
    is_elite = rng.random() < D.get("elite_chance") and force_behavior is None
    if force_behavior:
        row = next(r for r in BEHAVIOR_TABLE if r[0] == force_behavior)
    else:
        weights = [r[1] * (behavior_bias or {}).get(r[0], 1.0)
                   for r in BEHAVIOR_TABLE]
        #row = rng.choices(BEHAVIOR_TABLE, weights=[r[1] for r in BEHAVIOR_TABLE], k=1)[0]
        row = rng.choices(BEHAVIOR_TABLE, weights=weights, k=1)[0]
    behavior, _, hp_mult, dmg_mult, speed = row

   
    
    area_diff = area_difficulty_mult(area_level)

    #dmg = int((6 + area_level * 1.6) * dmg_mult * (1.7 if is_elite else 1.0)
    #      * D.get("enemy_damage_mult") * config.ENEMY_DAMAGE_GLOBAL_SCALE)
    #hp  = int((32 + area_level * 9)  * hp_mult * (2.1 if is_elite else 1.0) * D.get("enemy_hp_mult"))
    #dmg = int((9  + area_level * 2.2) * dmg_mult * (1.7 if is_elite else 1.0) * D.get("enemy_damage_mult"))
    
    dmg = int((6 + area_level * 1.6) * dmg_mult * (1.7 if is_elite else 1.0)
              * D.get("enemy_damage_mult") * config.ENEMY_DAMAGE_GLOBAL_SCALE
              * area_diff)
    hp  = int((52 + area_level * 12)  * hp_mult * (2.1 if is_elite else 1.0)
              * D.get("enemy_hp_mult") * area_diff)

    #was (32 + area_level * 9) 

    speed = speed * D.get("enemy_speed_mult")
    
    base_name = rng.choice(NAME_POOL[behavior])

    affixes = []
    if is_elite:
        k = 2 if (area_level >= 6 and rng.random() < 0.35) else 1
        affixes = rng.sample(ELITE_AFFIXES, k)

    personality = rng.choices(
        [p[0] for p in PERSONALITY_POOL],
        weights=[p[1] for p in PERSONALITY_POOL], k=1)[0]
    #name = f"{affixes[0].title()} {base_name}" if affixes else base_name
    prefix = PERSONALITY_PREFIX.get(personality, "")
    name = f"{affixes[0].title()} {prefix}{base_name}" if affixes else f"{prefix}{base_name}"

    base_xp = 18 if is_elite else 10
    xp = int(base_xp * (1.18 ** (area_level - 1)))
    #en = Enemy(x, y, name=name, hp=hp, damage=dmg, armor=area_level,
    #           speed=speed, xp_reward=xp if is_elite else 10,
    #           is_elite=is_elite, area_level=area_level, behavior=behavior)

    en = Enemy(x, y, name=name, hp=hp, damage=dmg, armor=area_level,
           speed=speed, xp_reward=xp,
           is_elite=is_elite, area_level=area_level, behavior=behavior)
    
    en._aggro_range = int(en._aggro_range * D.get("enemy_aggro_mult"))
    if behavior == "brute":
        en.width = en.height = 40
    elif behavior == "swarmer":
        en.width = en.height = 20

    if "swift" in affixes:
        en.speed *= 1.5
    if "armored" in affixes:
        en.armor = 20 + area_level * 6
        en.resistances["armor_flat"] = en.armor
        en.max_hp = en.hp = int(en.max_hp * 1.3)
    if "shielded" in affixes:
        en.shield_hp = int(en.max_hp * 0.4)
    en.affixes = affixes
    en.personality = personality
    if personality == "swift":
        en.speed *= 1.25
        en.max_hp = en.hp = int(en.hp * 0.85)
    return en
