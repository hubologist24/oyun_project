"""Combat: hits, crits, attacks, projectiles, phase transitions, death."""
import math

import core.config as config
from core import audio
from core.difficulty import D
from combat.combat import player_attack_enemy
from enemies.dialogue import boss_phase_line, boss_death_taunt
from world import swing_fx
from core.game_parts.constants import CRIT_BURST_RADIUS, CRIT_BURST_RATIO

from combat.attack_patterns import PlayerProjectile

import random
import pygame




CHAIN_RADIUS          = 220.0
CHAIN_DAMAGE_MULT     = 0.70
FORK_CHILD_DMG_MULT   = 0.60
FORK_CHILD_RANGE_MULT = 0.50
FORK_MAX_GENERATIONS  = 2


class CombatMixin:

    def _damage_type_color(self, dtype):
        return {
            "fire":      (255, 130, 40),
            "cold":      (120, 200, 255),
            "lightning": (255, 240, 120),
            "chaos":     (200, 90, 255),
            "physical":  (220, 220, 220),
            "poison":    (140, 230, 120),
            "bleed":     (220, 60, 60),
        }.get(dtype, (255, 255, 255))

    def _on_critical_hit(self, x, y, damage_type, crit_damage, enemy=None, **kwargs):
        """Elemental crit detonation: splash a small AoE of the same damage
        type around the struck enemy."""
        from combat.combat import _enemy_category
        from combat.damage import resolve_damage
        if enemy is not None and self.player.has_keystone("crit_echo"):
            self._echoes.append({"t": 0.4, "enemy": enemy, "dtype": damage_type,
                                 "dmg": max(1, int(crit_damage * 0.4))})

        rules = self._active_world_rules()
        explosion_damage = max(1, int(crit_damage * CRIT_BURST_RATIO))
        radius = CRIT_BURST_RADIUS

        # Splash to nearby enemies (not the original target).
        for other in list(self.current_area.enemies):
            if not other.alive:
                continue
            d = math.hypot(other.x - x, other.y - y)
            if d < 1.0 or d > radius:
                continue
            dmg = resolve_damage(explosion_damage, damage_type, None,
                                 target_resistances=other.resistances,
                                 world_rules=rules,
                                 target_category=_enemy_category(other),
                                 source_category="player")
            other.take_damage(dmg)
            self.damage_numbers.spawn(other.x, other.y - 25, str(dmg),
                                      self._damage_type_color(damage_type))
            if not other.alive:
                self._on_enemy_killed(other)

        # Also splash onto bosses if they're in range.
        for boss in self.current_area.bosses:
            if not boss.alive:
                continue
            if math.hypot(boss.x - x, boss.y - y) > radius:
                continue
            dmg = resolve_damage(explosion_damage, damage_type, None,
                                 target_resistances=boss.resistances,
                                 world_rules=rules,
                                 target_category="boss",
                                 source_category="player")
            boss.take_damage(dmg)
            self.damage_numbers.spawn(boss.x, boss.y - 40, str(dmg),
                                      self._damage_type_color(damage_type))

            if getattr(boss, "phase_just_changed", False):
                boss.phase_just_changed = False
                self._trigger_phase_transition(boss)

            if not boss.alive:
                self._on_boss_killed(boss)

    def _tick_echoes(self, dt):
        for e in list(self._echoes):
            e["t"] -= dt
            if e["t"] > 0:
                continue
            self._echoes.remove(e)
            t = e["enemy"]
            if not t.alive:
                continue
            t.take_damage(e["dmg"])
            self.damage_numbers.spawn(t.x, t.y - 35, f"{e['dmg']} echo",
                                      self._damage_type_color(e["dtype"]))
            if getattr(t, "phase_just_changed", False):
                t.phase_just_changed = False
                self._trigger_phase_transition(t)
            if not t.alive:
                if getattr(t, "is_boss", False):
                    self._on_boss_killed(t)
                else:
                    self._on_enemy_killed(t)

    def _on_player_hit_feedback(self, source=None, damage=0, damage_type=None):
        self.damage_numbers.spawn(self.player.x, self.player.y - 30, str(damage), (255, 90, 90))
        self.camera.shake(magnitude=6, duration=0.20)
        self._trigger_hitstop(0.04)
        self.player.combo_count = 0
        self.player.combo_timer = 0.0
        audio.play("hit_player")

        # --- status effect application ---
        if not self.player.alive:
            return
        if damage_type in ("fire", "chaos", "poison"):
            self.player.apply_burn(damage_type, damage)
        elif damage_type == "cold":
            self.player.apply_slow(2.0, 0.6)
        elif damage_type == "lightning":
            self.player.apply_stagger(0.35)

    def _trigger_hitstop(self, duration: float):
        # Cap so a burst of hits can't lock the game.
        self._hitstop_timer = min(0.12, max(self._hitstop_timer, duration))

    def _nearest_boss(self, max_dist=700):
        best, best_d = None, max_dist
        for b in self.current_area.bosses:
            if not b.alive:
                continue
            d = math.hypot(b.x - self.player.x, b.y - self.player.y)
            if d < best_d:
                best, best_d = b, d
        return best


    def _try_wall_slam(self, target, techniques, world_rules):
        """If the player just did a dash_strike and the target is pinned between
        the player and a wall, deal a bonus physical hit and stagger."""
        if "dash_strike" not in techniques:
            return False
        if getattr(target, "is_boss", False):
            return False  # bosses are too heavy to be pinned

        from combat.combat import _enemy_category
        from combat.damage import resolve_damage

        dx = target.x - self.player.x
        dy = target.y - self.player.y
        d = math.hypot(dx, dy) or 1.0
        nx, ny = dx / d, dy / d

        check_dist = max(target.width, target.height) * 0.5 + config.TILE_SIZE * 0.6
        check_x = target.x + nx * check_dist
        check_y = target.y + ny * check_dist

        tm = self.current_area.tilemap
        ts = config.TILE_SIZE
        col = int(check_x // ts)
        row = int(check_y // ts)
        if not tm.is_wall(col, row):
            return False

        slam_base = max(4, int(target.max_hp * 0.06))
        dmg = resolve_damage(slam_base, "physical", None,
                            target_resistances=target.resistances,
                            world_rules=world_rules,
                            target_category=_enemy_category(target),
                            source_category="player")
        target.take_damage(dmg)
        if hasattr(target, "apply_stagger"):
            target.apply_stagger(1.8)

        self.damage_numbers.spawn(target.x, target.y - 70, "WALL SLAM!", (255, 180, 80))
        self.crit_bursts.append({"x": target.x, "y": target.y, "radius": 90,
                                "life": 0.4, "max_life": 0.4, "color": (255, 180, 80)})
        self.camera.shake(magnitude=6, duration=0.2)
        self.event_bus.emit("skill_used", skill="wall_slam", damage=dmg,
                            target=target.name, damage_type="physical",
                            weapon_name=None, is_crit=False)

        if not target.alive:
            if getattr(target, "is_boss", False):
                self._on_boss_killed(target)
            else:
                self._on_enemy_killed(target)
        return True
  

    def _live_attack_targets(self):
        targets = [e for e in self.current_area.enemies if e.alive]
        targets.extend(b for b in self.current_area.bosses if b.alive)
        return targets

    def _apply_player_hit(self, target, world_rules, damage_multiplier=1.0, techniques=None,is_melee=False):
        if (self.player.has_keystone("executioner") and getattr(target, "max_hp", 0)
                and target.hp < target.max_hp * 0.3):
            damage_multiplier *= 1.5

        if self._consume_perfect_dodge_counter():
            damage_multiplier *= 1.5
            self.damage_numbers.spawn(self.player.x, self.player.y - 70,
                                    "COUNTER!", (255, 240, 120))
            self.crit_bursts.append({
                "x": self.player.x, "y": self.player.y, "radius": 60,
                "life": 0.3, "max_life": 0.3, "color": (255, 240, 120),
            })


        techniques = list(techniques or [])
        pierce = "dash_strike" in techniques
        is_boss = getattr(target, "is_boss", False)
        if is_boss:
            if not self._boss_attempt_active and not getattr(target, "is_nemesis", False):
                self._boss_attempt_active = True
                self._boss_attempt_boss_id = target.name
                self._boss_deaths_this_attempt = 0
                self.event_bus.emit("boss_attempt_started", boss_id=target.name)
                self._boss_greeting(target)
            dmg = player_attack_enemy(self.player, target, self.event_bus,
                                      world_rules=world_rules,
                                      damage_multiplier=damage_multiplier, pierce=pierce)
            if dmg > 0:
                self._trigger_hitstop(0.06)
            self.camera.shake(magnitude=4, duration=0.12)
            audio.play("hit_boss")
            self.damage_numbers.spawn(target.x, target.y - 40, str(dmg), (255, 215, 90))
            if techniques:
                self.kinetic.trigger(techniques, target)
            if target.alive:
                self.reactions.on_hit(target, dmg, self.player, world_rules)
            if getattr(target, "phase_just_changed", False):
                target.phase_just_changed = False
                self._trigger_phase_transition(target)

            if getattr(target, "just_broke", False):
                target.just_broke = False
                self._show_message(f"{target.name} is BROKEN! Strike now!", duration=1.5)
                self.crit_bursts.append({
                    "x": target.x, "y": target.y, "radius": 85,
                    "life": 0.45, "max_life": 0.45, "color": (255, 200, 60),
                })
            
            self.event_bus.emit("boss_attempt_damage")

            if is_melee and dmg > 0:
                self._try_melee_procs(target, dmg, world_rules)
            
            

            if not target.alive:
                self._on_boss_killed(target)
        else:
            dmg = player_attack_enemy(self.player, target, self.event_bus,
                                      world_rules=world_rules,
                                      damage_multiplier=damage_multiplier, pierce=pierce)
            self.damage_numbers.spawn(target.x, target.y - 25, str(dmg), (255, 255, 255))
            if techniques:
                self.kinetic.trigger(techniques, target)
            if target.alive:
                self.reactions.on_hit(target, dmg, self.player, world_rules)

            #if is_melee and dmg > 0:
            #    self._try_melee_procs(target, dmg, world_rules)

            if is_melee and dmg > 0:
                self._try_wall_slam(target, techniques, world_rules)
                self._try_melee_procs(target, dmg, world_rules)

            if getattr(target, "just_broke", False):
                target.just_broke = False
                self._show_message(f"{target.name} is BROKEN! Strike now!", duration=1.5)
                self.crit_bursts.append({
                    "x": target.x, "y": target.y, "radius": 85,
                    "life": 0.45, "max_life": 0.45, "color": (255, 200, 60),
                })

            if not target.alive:
                self._on_enemy_killed(target)

    def _trigger_phase_transition(self, boss):
        self._trigger_hitstop(0.18)
        self.camera.shake(magnitude=10, duration=0.35)
        audio.play("evolution")
        self._show_message(
            f"{boss.name} enters Phase {boss.phase_index + 1}!",
            duration=1.6,
        )
        self.crit_bursts.append({
            "x": boss.x, "y": boss.y,
            "radius": 140,
            "life": 0.55, "max_life": 0.55,
            "color": (255, 90, 200),
        })
        self._alter_boss_arena(boss)   # NEW

        if not getattr(boss, "is_nemesis", False):
            self._say(boss.name, boss_phase_line(boss.phase_index), 3.0)


    def _alter_boss_arena(self, boss):
        """On phase change, boss paints the arena with hazards of its element."""
        dtype = getattr(boss, "damage_type", "physical")
        surface_kind = {
            "fire": "fire",
            "cold": "ice",
            "lightning": "static",
            "chaos": "blight",
            "poison": "blight",
            "bleed": "rot",
            "physical": "rot",
        }.get(dtype)
        if surface_kind is None:
            return

        rng = self.world.rng_service.get_stream("boss_arena")
        count = 1 + min(2, boss.phase_index)
        for _ in range(count):
            angle = rng.uniform(0, math.tau)
            dist = rng.uniform(120, 280)
            x = boss.x + math.cos(angle) * dist
            y = boss.y + math.sin(angle) * dist
            self.terrain.add_surface(
                x, y, 80, surface_kind, 8.0,
                power=max(6, int(boss.max_hp * 0.02)),
            )
        self._show_message(f"The arena warps around {boss.name}!", duration=2.0)


    def _execute_combo_finisher(self, world_rules):
        """Triggered at combo_count >= 10. Weapon-shaped AoE burst. Resets combo."""
        from combat.combat import player_attack_enemy

        weapon = self.player.equipped.get("weapon")
        subtype = getattr(weapon, "weapon_subtype", None) if weapon else None

        radius = 160
        dmg_mult = 1.8
        stagger = 0.6
        color = (255, 180, 255)

        if subtype == "greatsword":
            radius, dmg_mult, stagger = 180, 2.5, 1.2
        elif subtype == "sword":
            dmg_mult, stagger = 2.2, 0.5
        elif subtype in ("bow", "gun", "short_wand", "long_wand"):
            radius, dmg_mult, stagger = 200, 1.8, 0.3

        self.camera.shake(magnitude=8, duration=0.3)
        self._trigger_hitstop(0.10)
        self.crit_bursts.append({
            "x": self.player.x, "y": self.player.y, "radius": radius,
            "life": 0.5, "max_life": 0.5, "color": color,
        })
        self.damage_numbers.spawn(self.player.x, self.player.y - 80,
                                "FINISHER!", color)
        audio.play("evolution")

        for t in self._live_attack_targets():
            if math.hypot(t.x - self.player.x, t.y - self.player.y) > radius:
                continue
            dmg = player_attack_enemy(self.player, t, self.event_bus,
                                    world_rules=world_rules,
                                    damage_multiplier=dmg_mult)
            if stagger and hasattr(t, "apply_stagger"):
                t.apply_stagger(stagger)
            self.damage_numbers.spawn(t.x, t.y - 25, str(dmg), color)
            if t.alive:
                self.reactions.on_hit(t, dmg, self.player, world_rules)
            if not t.alive:
                if getattr(t, "is_boss", False):
                    self._on_boss_killed(t)
                else:
                    self._on_enemy_killed(t)
    

    def _try_attack(self):
        if not self.player.alive or not self.player.can_attack():
            return

        from combat.attack_patterns import find_arc_targets, spawn_projectile

        profile = self.player.current_attack_profile()
        self.player.start_attack_cooldown(profile)
        world_rules = self._active_world_rules()

        if profile.kind == "projectile":
            proj = spawn_projectile(
                self.player, profile,
                damage_type=self.player.effective_stats.primary_damage_type,
                world_rules=world_rules,
            )
            proj.technique = self.kinetic.classify_ranged(self.player)
            self.player_projectiles.append(proj)
            weapon = self.player.equipped.get("weapon")
            self.event_bus.emit("skill_used", skill="basic_attack", damage=0, target=None,
                                damage_type=self.player.effective_stats.primary_damage_type,
                                weapon_name=weapon.display_name if weapon else None)
            return

        swing_tech = self.kinetic.classify_melee(self.player, profile)
        if swing_tech == "reverse_slash":
            profile = self.kinetic.widen(profile)

        # Spawn the arc/cone FX before checking hits so a whiffed swing still shows.
        swing_fx.spawn_swing(self.player_swings, self.player.x, self.player.y,
                             self.player.facing, profile)

        targets = self._live_attack_targets()
        if self.player.combo_count >= 10:
            # FINISHER consumes the combo
            self._execute_combo_finisher(world_rules)
            self.player.combo_count = 0
            self.player.combo_timer = 0.0
            return

        hits = find_arc_targets(self.player, targets, profile)
        if not profile.hits_all:
            hits = hits[:1]

        if hits:
            self.player.combo_count += 1
            self.player.combo_timer = 2.0
            if self.player.combo_count == 10:
                self._show_message(
                    "FINISHER READY! Next attack unleashes your weapon.",
                    duration=2.5)
                self.crit_bursts.append({
                    "x": self.player.x, "y": self.player.y, "radius": 90,
                    "life": 0.4, "max_life": 0.4,
                    "color": (255, 210, 120),
                })

        combo_mult = 1.0 + min(self.player.combo_count, 10) * 0.06

        if hits and self.player.combo_count % 5 == 0 and self.player.has_keystone("rhythm"):
            for t in hits:
                self.reactions.expose(t, 3.0)
            self._show_message("RHYTHM!", duration=0.8)

        for target in hits:
            techs = [swing_tech] if swing_tech else []
            if self.kinetic.is_intercept(target):
                techs.append("intercept")
            self._apply_player_hit(target, world_rules,
                                   profile.damage_multiplier * combo_mult,
                                   techs, is_melee=True)

    def _update_player_projectiles(self, dt):
        area = self.current_area
        #for proj in list(self.player_projectiles):
        #    proj.update(dt)

        #    if not proj.alive:
        #        self.player_projectiles.remove(proj)
        #        continue
        #    if area.tilemap.collides_rect(proj.rect):
        #        self.player_projectiles.remove(proj)
        #        continue
        for proj in list(self.player_projectiles):
            prev_x, prev_y = proj.x, proj.y
            proj.update(dt)

            if not proj.alive:
                self.player_projectiles.remove(proj)
                continue
            if area.tilemap.collides_rect(proj.rect):
                if (proj.ricochet_count > 0
                        and self._try_projectile_ricochet(proj, prev_x, prev_y, dt)):
                    proj.ricochet_count -= 1
                    self._spawn_ricochet_effect(proj)
                    continue
                self.player_projectiles.remove(proj)
                continue

            all_targets = list(area.enemies) + list(area.bosses)
            consumed = False

            for target in all_targets:
                if not target.alive or id(target) in proj.hit_ids:
                    continue
                if not proj.rect.colliderect(target.rect):
                    continue

                proj.hit_ids.add(id(target))
                techs = self.kinetic.projectile_techniques(proj)
                self._apply_player_hit(target, proj.world_rules,
                                       proj.damage_multiplier, techs)
                self._projectile_on_hit_effects(proj, target)

                # Chain — redirect the projectile toward nearest unhit target
                if self._try_projectile_chain(proj, all_targets):
                    continue
                # Pierce — walk through to next enemy
                if proj.pierce_remaining > 0:
                    proj.pierce_remaining -= 1
                    continue
                # Perfect-range free pierce
                if "perfect_range" in techs and not proj.bonus_used:
                    proj.bonus_used = True
                    continue
                consumed = True
                break

            if consumed and proj in self.player_projectiles:
                self.player_projectiles.remove(proj)


    def _try_projectile_ricochet(self, proj, prev_x, prev_y, dt) -> bool:
        """Reverse velocity on the axis that hit a wall. Returns True if bounced."""
        tilemap = self.current_area.tilemap
        r = proj.radius

        def blocked(x, y):
            return tilemap.collides_rect(
                pygame.Rect(int(x - r), int(y - r), r * 2, r * 2))

        nx = prev_x + proj.vx * dt
        ny = prev_y + proj.vy * dt
        x_blocked = blocked(nx, prev_y)
        y_blocked = blocked(prev_x, ny)
        if not x_blocked and not y_blocked:
            return False

        # Snap back to the last safe position, then redirect.
        proj.x, proj.y = prev_x, prev_y
        speed = math.hypot(proj.vx, proj.vy) or 1.0

        if self.player.has_keystone("trick_shot"):
            targets = [t for t in (self.current_area.enemies + self.current_area.bosses)
                       if t.alive and math.hypot(t.x - proj.x, t.y - proj.y) < 400]
            if targets and random.random() < 0.5:
                target = random.choice(targets)
                dx, dy = target.x - proj.x, target.y - proj.y
                d = math.hypot(dx, dy) or 1.0
                proj.vx = dx / d * speed
                proj.vy = dy / d * speed
            else:
                angle = random.uniform(0.0, 2.0 * math.pi)
                proj.vx = math.cos(angle) * speed
                proj.vy = math.sin(angle) * speed
        else:
            if x_blocked and y_blocked:
                proj.vx = -proj.vx
                proj.vy = -proj.vy
            elif x_blocked:
                proj.vx = -proj.vx
            else:
                proj.vy = -proj.vy

        # Give the ricochet room to actually fly before dying.
        proj.remaining_range += 100
        return True

    def _spawn_ricochet_effect(self, proj):
        self.crit_bursts.append({
            "x": proj.x, "y": proj.y, "radius": 25,
            "life": 0.15, "max_life": 0.15,
            "color": (255, 220, 120),
        })
        self.damage_numbers.spawn(proj.x, proj.y - 20, "RICOCHET", (255, 220, 120))


    def _projectile_on_hit_effects(self, proj, hit_target):
        if proj.fork_count > 0 and proj.fork_generation < FORK_MAX_GENERATIONS:
            self._spawn_forks(proj, hit_target)

    def _spawn_forks(self, proj, hit_target):
        base_angle = math.atan2(proj.vy, proj.vx)
        speed = math.hypot(proj.vx, proj.vy) or 500.0
        spread = math.radians(25)
        n = proj.fork_count
        for i in range(n):
            offset = (i - (n - 1) / 2.0) * spread
            angle = base_angle + offset
            child = PlayerProjectile(
                x=hit_target.x, y=hit_target.y,
                vx=math.cos(angle) * speed * 0.85,
                vy=math.sin(angle) * speed * 0.85,
                damage_type=proj.damage_type,
                damage_multiplier=proj.damage_multiplier * FORK_CHILD_DMG_MULT,
                world_rules=proj.world_rules,
                piercing=False,
                max_range=proj.max_range * FORK_CHILD_RANGE_MULT,
            )
            child.pierce_remaining = 0
            child.fork_count       = 0
            child.chain_count      = 1          # children can chain once
            child.fork_generation  = proj.fork_generation + 1
            child.hit_ids          = set(proj.hit_ids)
            child.technique        = None
            self.player_projectiles.append(child)

        self.damage_numbers.spawn(hit_target.x, hit_target.y - 60, "FORK", (200, 240, 255))
        self.crit_bursts.append({"x": hit_target.x, "y": hit_target.y,
                                 "radius": 40, "life": 0.18, "max_life": 0.18,
                                 "color": (200, 240, 255)})

    def _try_projectile_chain(self, proj, all_targets):
        if proj.chain_count <= 0:
            return False
        candidates = []
        for t in all_targets:
            if not t.alive or id(t) in proj.hit_ids:
                continue
            d = math.hypot(t.x - proj.x, t.y - proj.y)
            if d <= CHAIN_RADIUS:
                candidates.append((d, t))
        if not candidates:
            return False
        candidates.sort(key=lambda p: p[0])
        next_t = candidates[0][1]
        dx, dy = next_t.x - proj.x, next_t.y - proj.y
        d = math.hypot(dx, dy) or 1.0
        speed = math.hypot(proj.vx, proj.vy) or 520.0
        proj.vx = dx / d * speed
        proj.vy = dy / d * speed
        proj.remaining_range = max(proj.remaining_range, CHAIN_RADIUS * 1.5)
        proj.chain_count -= 1
        proj.damage_multiplier *= CHAIN_DAMAGE_MULT

        self.damage_numbers.spawn(proj.x, proj.y - 20, "CHAIN", (140, 220, 255))
        self.crit_bursts.append({"x": proj.x, "y": proj.y, "radius": 35,
                                 "life": 0.15, "max_life": 0.15,
                                 "color": (140, 220, 255)})
        return True

    # --- melee-small procs ---------------------------------------------------

    def _try_melee_procs(self, target, damage, world_rules):
        rng = self.world.rng_service.get_stream("melee_procs")
        procs = (
            ("proc_chain_lightning", self._proc_chain_lightning),
            ("proc_siphon",          self._proc_siphon),
            ("proc_shatter",         self._proc_shatter),
        )
        for proc_id, handler in procs:
            chance = self.player.melee_proc_chance(proc_id)
            if chance <= 0:
                continue
            if rng.random() < chance:
                handler(target, damage, world_rules)
                break   # at most one proc per hit

    def _proc_chain_lightning(self, target, damage, world_rules):
        from combat.combat import _enemy_category
        from combat.damage import resolve_damage
        radius = 200.0
        zapped = 0
        for other in list(self.current_area.enemies) + list(self.current_area.bosses):
            if other is target or not other.alive:
                continue
            if math.hypot(other.x - target.x, other.y - target.y) > radius:
                continue
            dmg = resolve_damage(max(1, int(damage * 0.4)), "lightning", None,
                                 target_resistances=other.resistances,
                                 world_rules=world_rules,
                                 target_category=_enemy_category(other),
                                 source_category="player")
            other.take_damage(dmg)
            self.damage_numbers.spawn(other.x, other.y - 30, str(dmg),
                                      (140, 200, 255))
            self.crit_bursts.append({"x": other.x, "y": other.y, "radius": 50,
                                     "life": 0.20, "max_life": 0.20,
                                     "color": (140, 200, 255)})
            if getattr(other, "phase_just_changed", False):
                other.phase_just_changed = False
                self._trigger_phase_transition(other)
            if not other.alive:
                if getattr(other, "is_boss", False):
                    self._on_boss_killed(other)
                else:
                    self._on_enemy_killed(other)
            zapped += 1
        if zapped:
            self.damage_numbers.spawn(target.x, target.y - 52, "SPARKSTRIKE",
                                      (140, 200, 255))

    def _proc_siphon(self, target, damage, world_rules):
        stats = self.player.effective_stats
        heal = max(1, int(stats.max_hp * 0.04))
        stats.hp = min(stats.max_hp, stats.hp + heal)
        self.damage_numbers.spawn(self.player.x, self.player.y - 34,
                                  f"+{heal}", (90, 255, 120))
        self.damage_numbers.spawn(target.x, target.y - 52, "SIPHON", (90, 255, 120))

    def _proc_shatter(self, target, damage, world_rules):
        self.reactions.expose(target, 2.0)
        self.damage_numbers.spawn(target.x, target.y - 52, "SHATTERED",
                                  (255, 220, 100))
    

    def _clear_player_projectiles(self):
        self.player_projectiles.clear()

    def _on_player_death(self, cause=None):
        if self._boss_attempt_active:
            self._boss_deaths_this_attempt += 1

        lost = self.player.progression.lose_xp_progress(D.get("death_xp_penalty"))
        msg = "You died. Respawning..."
        if lost > 0:
            msg = f"You died (-{lost} XP progress). Respawning..."
        self._show_message(msg)

        if self._boss_attempt_active:
            b = self._nearest_boss(1500)
            if b:
                self._say(b.name, boss_death_taunt(self._current_profile().main_damage))
            self.event_bus.emit("boss_attempt_ended", result="death",
                                boss_id=self._boss_attempt_boss_id)
            self._boss_attempt_active = False
            self._boss_attempt_boss_id = None

        # --- Adaptive Difficulty Tracking ---
        self._deaths_since_boss_kill = getattr(self, "_deaths_since_boss_kill", 0) + 1
        if (getattr(config, "DIFFICULTY_ADAPTIVE", False)
                and self._deaths_since_boss_kill >= 3):
            D.adjust(-getattr(config, "DIFFICULTY_ADAPTIVE_DOWN", 3))
            self._deaths_since_boss_kill = 0
            self._show_message(f"Difficulty adjusts: {D.label()}", duration=2.0)

        self.nemesis.on_player_death(cause)

    def _respawn_player(self):
        self.player.x, self.player.y = self.current_area.region_start.get(
            self.current_area_id, self.current_area.start_pos)
        self.player.alive = True
        self.player.heal_to_full()
        self.terrain.clear_hostile()
        self._respawn_timer = 1.5
        self._show_message("Respawned.")
