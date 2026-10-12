import math
import pygame
from dataclasses import asdict

import core.config as config
from history.nemesis import Nemesis, NemesisRegistry, make_name, taunt_for
from enemies.enemy import spawn_basic_enemy
from enemies.boss import Boss, BossTemplate
from world.loot_rules import LootRules
from combat.damage import resolve_damage

RESPAWN_DELAY = 180.0        # playtime seconds before a slain nemesis returns stronger
MAX_GENERATION = 5
WORLD_BOSS_KILLS = 3
NOVA_RADIUS = 130
NOVA_DELAY = 0.85            # telegraph time (fairness floor)


class NemesisManager:
    def __init__(self, game):
        self.game = game
        self.registry = NemesisRegistry()
        self.reset_transient()

    def reset_transient(self):
        self.last_attacker = None
        self.last_attacker_time = -99.0
        self.novas = []
        self._check_timer = 0.0

    def _now(self):
        return self.game.world.total_playtime_seconds

    def _rng(self):
        return self.game.world.rng_service.get_stream("nemesis")

    # ---------------- event hooks ----------------
    def on_attacker_info(self, enemy=None, **kw):
        self.last_attacker = enemy
        self.last_attacker_time = self._now()

    def on_player_death(self, cause=None):
        a = self.last_attacker
        self.last_attacker = None
        if (a is None or getattr(a, "is_boss", False) or getattr(a, "is_add", False)
                or not a.alive or self._now() - self.last_attacker_time > 3.0):
            return
        cause = cause or "physical"
        nem = getattr(a, "nemesis", None)
        g = self.game
        if nem is None:
            nem = Nemesis(name=make_name(cause, self._rng()), damage_type=cause,
                          behavior=a.behavior, area_level=a.area_level)
            nem.taunt = taunt_for(cause, 1, self._rng())
            self.registry.add(nem)
            self._apply(a, nem)
            g._show_message(f"{nem.name} has marked you!", duration=4.0)
        else:
            nem.kills_on_player += 1
            nem.taunt = taunt_for(nem.damage_type, nem.kills_on_player, self._rng())
            if nem.kills_on_player >= WORLD_BOSS_KILLS and not nem.world_boss:
                self._make_world_boss(a, nem)
            else:
                self._apply(a, nem)
        g._say(nem.short(), nem.taunt, 5.0)

    def on_area_entered(self, area_id=None, **kw):
        g = self.game
        if area_id is None:
            return
        for e in self._in_play():
            if getattr(e, "is_boss", False):
                continue
            if g.current_area.region_at_pixel(e.x, e.y) == area_id:
                continue
            spot = self._spot_near_player(area_id)
            if spot and self._rng().random() < 0.6:
                e.x, e.y = spot
                e.region_id = area_id
                g._show_message(f"{e.nemesis.short()} is hunting you...", duration=3.0)
                break

    # ---------------- promotion / scaling ----------------
    def _apply(self, enemy, nem):
        if not hasattr(enemy, "_nem_base"):
            enemy._nem_base = (enemy.max_hp, enemy.damage)
        bhp, bdmg = enemy._nem_base
        scale = 1 + 0.3 * (nem.generation - 1) + 0.2 * (nem.kills_on_player - 1)
        enemy.nemesis = nem
        enemy.name = nem.display()
        enemy.is_elite = True
        enemy.max_hp = int(bhp * 2.2 * scale)
        enemy.hp = enemy.max_hp
        enemy.damage = int(bdmg * 1.4 * scale)
        enemy.damage_type = nem.damage_type
        enemy.resistances[nem.damage_type] = 0.3
        enemy.xp_reward = 60 * nem.generation
        enemy._aggro_range = 700
        enemy.width = enemy.height = max(enemy.width, 34)
        enemy.alive = True

    def _build_boss(self, x, y, nem, rid):
        from enemies.boss_factory import build_personalized_boss_template
        from ai.validators import BossValidator
        g = self.game
        lvl = max(nem.area_level, g.player.progression.level) + 3
        t = build_personalized_boss_template(
            nem.name, {"mechanic_archetype": nem.archetype, "difficulty_bias": "normal"},
            lvl, self._rng())
        t.damage_type = nem.damage_type
        t.resistances[nem.damage_type] = 0.3
        ok, errs = BossValidator.validate(asdict(t))
        if not ok:
            print(f"[nemesis] boss failed validation {errs}; using safe default")
            t = BossTemplate.hollow_warden()
            t.name = nem.name
            t.damage_type = nem.damage_type
        b = Boss(x, y, t)
        b.region_id = rid
        b.is_nemesis = True
        b.nemesis = nem
        return b

    def _make_world_boss(self, enemy, nem):
        g = self.game
        nem.world_boss = True
        nem.archetype = self._rng().choice(["dash_predator", "berserker", "reflector"])
        boss = self._build_boss(enemy.x, enemy.y, nem, getattr(enemy, "region_id", "starting_area"))
        if enemy in g.current_area.enemies:
            g.current_area.enemies.remove(enemy)
        g.current_area.bosses.append(boss)
        g._show_message(f"{nem.short()} has become a WORLD BOSS!", duration=5.0)

    # ---------------- death / rewards ----------------
    def on_killed(self, victim):
        g, p = self.game, self.game.player
        nem = victim.nemesis
        gen_at_kill = nem.generation
        if nem.world_boss or nem.generation >= MAX_GENERATION:
            nem.status = "dead"
        else:
            nem.status = "pending"
            nem.generation += 1
            nem.respawn_at = self._now() + RESPAWN_DELAY
        p.trophies.append({"name": nem.name, "damage_type": nem.damage_type,
                           "generation": gen_at_kill})
        p.recalc_stats()

        rules = LootRules(favored_damage_types={nem.damage_type: 3.0},
                          rare_chance_multiplier=1.5, six_mod_chance_multiplier=1.1)
        lvl = max(p.progression.level, nem.area_level) + 2
        counts = [(3, 2), (2, 2), (2, 1)] + ([(3, 3)] if nem.world_boss else [])
        for i, c in enumerate(counts):
            g.current_area.spawn_loot(victim.x + (i - 1.5) * 30, victim.y, area_level=lvl,
                                      world_rules=g._active_world_rules(),
                                      loot_rules_override=rules, force_affix_counts=c)
        g.current_area.spawn_pickup(victim.x, victim.y + 30, "stone")
        g.current_area.spawn_orb(victim.x + 30, victim.y + 30)
        g._show_message(f"NEMESIS SLAIN: {nem.name}! Trophy earned "
                        f"(+3% {nem.damage_type} res, +5 life).", duration=6.0)
        g._say(nem.short(), "This... is not... the end.", 4.0)
        g.event_bus.emit("nemesis_slain", name=nem.name, generation=gen_at_kill)

    # ---------------- per-frame ----------------
    def _in_play(self):
        a = self.game.current_area
        return ([e for e in a.enemies if getattr(e, "nemesis", None) and e.alive]
                + [b for b in a.bosses if getattr(b, "nemesis", None) and b.alive])

    def _spot_near_player(self, rid):
        g, p = self.game, self.game.player
        ts = config.TILE_SIZE
        grid = g.current_area.tilemap.grid
        rng = self._rng()
        pc, pr = int(p.x // ts), int(p.y // ts)
        for _ in range(80):
            c, r = pc + rng.randint(-24, 24), pr + rng.randint(-16, 16)
            if not (0 <= r < len(grid) and 0 <= c < len(grid[0])) or grid[r][c] != 0:
                continue
            x, y = c * ts + ts / 2, r * ts + ts / 2
            d = math.hypot(x - p.x, y - p.y)
            if d < 520 or d > 1000:
                continue
            if g.current_area.region_at_tile(c, r) != rid:
                continue
            return x, y
        return None

    def _respawn_due(self):
        g = self.game
        for nem in self.registry.records:
            if nem.status != "pending" or self._now() < nem.respawn_at:
                continue
            rid = g.current_area_id
            spot = self._spot_near_player(rid)
            if not spot:
                continue
            if nem.world_boss:
                g.current_area.bosses.append(self._build_boss(spot[0], spot[1], nem, rid))
            else:
                e = spawn_basic_enemy(spot[0], spot[1], self._rng(),
                                      area_level=nem.area_level, force_behavior=nem.behavior)
                e.region_id = rid
                self._apply(e, nem)
                g.current_area.enemies.append(e)
            nem.status = "alive"
            g._show_message(f"{nem.short()} rises again, stronger than before.", duration=4.0)
            g._say(nem.short(), "Did you think I was finished?", 4.0)

    def update(self, dt):
        g, p = self.game, self.game.player
        if not p.alive:
            return
        now = self._now()
        self._check_timer -= dt
        if self._check_timer <= 0:
            self._check_timer = 1.0
            self._respawn_due()
            for e in self._in_play():
                if (math.hypot(e.x - p.x, e.y - p.y) < 520
                        and now - getattr(e, "_warn_at", -99) > 45):
                    e._warn_at = now
                    g._show_message(f"{e.nemesis.short().upper()} IS NEAR", duration=2.5)

        # signature nova: telegraphed ground circle in the killing damage type
        for e in self._in_play():
            if math.hypot(e.x - p.x, e.y - p.y) > 450:
                continue
            e._nova_t = getattr(e, "_nova_t", 3.5) - dt
            if e._nova_t <= 0:
                e._nova_t = 5.0 if e.nemesis.world_boss else 7.0
                base = e.template.melee_damage if getattr(e, "is_boss", False) else e.damage
                self.novas.append({"x": e.x, "y": e.y, "t": NOVA_DELAY,
                                   "dtype": e.nemesis.damage_type,
                                   "dmg": int(base * 1.3), "src": e})
        for n in list(self.novas):
            n["t"] -= dt
            if n["t"] > 0:
                continue
            self.novas.remove(n)
            color = g._damage_type_color(n["dtype"])
            g.crit_bursts.append({"x": n["x"], "y": n["y"], "radius": NOVA_RADIUS,
                                  "life": 0.3, "max_life": 0.3, "color": color})
            if math.hypot(p.x - n["x"], p.y - n["y"]) <= NOVA_RADIUS:
                s = p.effective_stats
                res = dict(s.resistances)
                res["armor_flat"] = s.armor
                dmg = resolve_damage(n["dmg"], n["dtype"], None, target_resistances=res,
                                     world_rules=g._active_world_rules(),
                                     target_category="player", source_category="elite")
                self.last_attacker, self.last_attacker_time = n["src"], now
                if p.take_damage(dmg, cause=n["dtype"]):
                    g.event_bus.emit("player_hit", source=n["src"].name,
                                     damage=dmg, damage_type=n["dtype"])

    def draw(self, surface, camera):
        g = self.game
        for n in self.novas:
            frac = 1 - n["t"] / NOVA_DELAY
            c = g._damage_type_color(n["dtype"])
            p = camera.world_to_screen((n["x"], n["y"]))
            pygame.draw.circle(surface, c, p, NOVA_RADIUS, 2)
            pygame.draw.circle(surface, c, p, max(2, int(NOVA_RADIUS * frac)), 1)
        for e in self._in_play():
            p = camera.world_to_screen((e.x, e.y))
            col = g._damage_type_color(e.nemesis.damage_type)
            pygame.draw.circle(surface, col, p, int(max(e.width, e.height) * 0.75), 2)
            label = g._info_font_b.render(e.nemesis.display(), True, col)
            surface.blit(label, (p[0] - label.get_width() // 2, p[1] - e.height // 2 - 46))

    # ---------------- gateway portal warning ----------------
    def regions_with_nemesis(self):
        out = {}
        for e in self._in_play():
            rid = self.game.current_area.region_at_pixel(e.x, e.y)
            out.setdefault(rid, []).append(e.nemesis.short())
        return out

    # ---------------- persistence ----------------
    def to_dict(self):
        return self.registry.to_dict()

    def load(self, d):
        self.registry = NemesisRegistry.from_dict(d)
        for n in self.registry.records:
            if n.status == "alive":          # enemies aren't saved; respawn shortly
                n.status = "pending"
                n.respawn_at = self._now() + 5.0
        self.reset_transient()