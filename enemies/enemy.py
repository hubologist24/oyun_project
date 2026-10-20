"""
Base enemy - Phase 2: adds a resistances dict (defaults empty = pure
armor-based mitigation, same as Phase 1 behavior) so future world
rules / enemy types can define per-type resistance profiles.
"""
import pygame
import random
import core.config as config
import math


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
        self._strafe_dir = 1 if random.random() < 0.5 else -1
        self._strafe_timer = 0.0
        self._ranged_timer = 1.2
        self.projectiles = []

        self.stagger_timer = 0.0
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

    _affix_font = None

    @property
    def rect(self) -> pygame.Rect:
        return pygame.Rect(int(self.x - self.width / 2), int(self.y - self.height / 2),
                            self.width, self.height)

    def take_damage(self, amount: int):
        if self.pack is not None:
            self.pack.alerted = True
        self.hp -= amount
        self.hit_flash_timer = 0.15
        if self.hp <= 0:
            self.hp = 0
            self.alive = False


    def apply_stagger(self, duration):
        self.stagger_timer = max(self.stagger_timer, duration)

    def apply_slow(self, duration, factor=0.5):
        self.slow_timer = max(self.slow_timer, duration)
        self.slow_factor = factor

    def interrupt(self):
        """Cancels the enemy's current attack windup."""
        self._attack_timer = max(self._attack_timer, 1.0)
        self._ranged_timer = max(self._ranged_timer, 1.5)
        self.apply_stagger(0.3)        

    def update(self, dt, player, tilemap, event_bus,world_rules=None):
        self._world_rules = world_rules

        if self.hit_flash_timer > 0:
            self.hit_flash_timer -= dt

        if not self.alive:
            return

        if "regenerating" in self.affixes and self.hp < self.max_hp:
            self.hp = min(self.max_hp, self.hp + self.max_hp * 0.04 * dt)

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
            "brute": self._tick_chaser,
            "swarmer": self._tick_chaser,
        }.get(self.behavior, self._tick_chaser)
        handler(dt, player, tilemap, dist, dx, dy, event_bus)

        self._tick_projectiles(dt, player, event_bus)

    def _rect_at(self, x, y):
        return pygame.Rect(int(x - self.width / 2), int(y - self.height / 2),
                            self.width, self.height)

    def draw(self, surface, camera):
        rect = camera.world_to_screen_rect(self.rect)
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


    def _contact_attack(self, player, event_bus):
        if self._attack_timer > 0:
            return
        from combat.combat import enemy_attack_player
        enemy_attack_player(self, player, event_bus,world_rules=self._world_rules)
        self._attack_timer = config.ENEMY_CONTACT_DAMAGE_COOLDOWN


    def _tick_chaser(self, dt, player, tilemap, dist, dx, dy, event_bus):
        if dist < self._aggro_range and dist > 34:
            self._step_toward(dt, tilemap, dx, dy, 1.0)
        elif dist <= 34:
            self._contact_attack(player, event_bus)


    def _tick_ambusher(self, dt, player, tilemap, dist, dx, dy, event_bus):
        # Dormant until the player commits to the room, then lunges fast.
        if dist > 220:
            return
        if dist > 60:
            self._step_toward(dt, tilemap, dx, dy, 0.4)   # creep
        elif dist > 34:
            self._step_toward(dt, tilemap, dx, dy, 2.2)   # lunge
        else:
            self._contact_attack(player, event_bus)


    def _tick_circler(self, dt, player, tilemap, dist, dx, dy, event_bus):
        # Prefers mid-range; strafes to stay there.
        if dist > self._aggro_range:
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
        # Kites: backs off under 160, closes past 300, else strafes and fires.
        if dist > self._aggro_range + 60:
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
            self._fire_bolt(dx, dy, dist)
            self._ranged_timer = 1.6


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
            "damage": int(self.damage * self.pack_damage_mult),
            "life": 3.0,
        })


    def _tick_projectiles(self, dt, player, event_bus):
        from combat.damage import resolve_damage
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
                dmg = resolve_damage(proj["damage"], self.damage_type, None,
                                    target_resistances=target_res,
                                    target_category="player", world_rules=self._world_rules)
                event_bus.emit("attacker_info", enemy=self)
                if player.take_damage(dmg, cause=self.damage_type):
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
NAME_POOL = {
    "chaser":   ["Feral Rat", "Bandit", "Wild Wolf", "Skeleton"],
    "ambusher": ["Lurker", "Cave Spider", "Stalker"],
    "circler":  ["Wisp", "Dust Shade", "Slinker"],
    "ranged":   ["Cultist", "Bone Archer", "Hexer"],
    "brute":    ["Ogre", "Bone Golem", "Crypt Brute"],
    "swarmer":  ["Carrion Bat", "Gnawer", "Imp"]
}


ELITE_AFFIXES = ["swift", "armored", "explosive", "regenerating"]
AFFIX_COLORS = {"swift": (90, 220, 255), "armored": (190, 190, 200),
                "explosive": (255, 140, 40), "regenerating": (110, 255, 130)}

def spawn_basic_enemy(x, y, rng, area_level=1, force_behavior=None):
    is_elite = rng.random() < 0.12 and force_behavior is None
    if force_behavior:
        row = next(r for r in BEHAVIOR_TABLE if r[0] == force_behavior)
    else:
        row = rng.choices(BEHAVIOR_TABLE, weights=[r[1] for r in BEHAVIOR_TABLE], k=1)[0]
    behavior, _, hp_mult, dmg_mult, speed = row

    #hp = int((24 + area_level * 6) * hp_mult * (1.8 if is_elite else 1.0))
    #dmg = int((6 + area_level * 1.6) * dmg_mult * (1.6 if is_elite else 1.0))
    hp  = int((32 + area_level * 9)  * hp_mult * (2.1 if is_elite else 1.0))
    dmg = int((9  + area_level * 2.2) * dmg_mult * (1.7 if is_elite else 1.0))
    
    base_name = rng.choice(NAME_POOL[behavior])

    affixes = []
    if is_elite:
        k = 2 if (area_level >= 6 and rng.random() < 0.35) else 1
        affixes = rng.sample(ELITE_AFFIXES, k)
    name = f"{affixes[0].title()} {base_name}" if affixes else base_name

    en = Enemy(x, y, name=name, hp=hp, damage=dmg, armor=area_level,
               speed=speed, xp_reward=18 if is_elite else 10,
               is_elite=is_elite, area_level=area_level, behavior=behavior)
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
    en.affixes = affixes
    return en
