# [PROJECT: tools] [ENTRY POINT]
# [SYMBOLS] classes: _NoKeys, CombatBotConfig, CombatIssue, CombatReport, CombatBot | functions: make_headless_game, _place_player_in_room, _clear_area, _spawn_wave, _spawn_elite, _spawn_boss, run_combat_scenario, _derive_seed, run_one, run_batch ...
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import random
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from typing import List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame                                                    # noqa: E402

import core.audio as _audio                                      # noqa: E402
_audio.init = lambda: None
_audio.play = lambda *a, **kw: None                              # noqa: E731

class _NoKeys:
    def __getitem__(self, k): return False
    def __iter__(self):       return iter(())
    def __len__(self):        return 0

pygame.key.get_pressed  = lambda: _NoKeys()                      # noqa: E731
pygame.mouse.get_pos    = lambda: (0, 0)                          # noqa: E731

import core.config as config                                     # noqa: E402
from core.game import Game                                       # noqa: E402


FATAL, ERROR, WARN = "FATAL", "ERROR", "WARN"
_SEV = {FATAL: 0, ERROR: 1, WARN: 2}


@dataclass
class CombatBotConfig:
    engage_range:            float = 640.0
    melee_stick_frac:        float = 0.85
    ranged_min:              float = 160.0
    ranged_max:              float = 340.0
    retreat_hp_frac:         float = 0.22
    dodge_telegraph:         bool  = True
    dodge_requires_charge:   bool  = True
    attack_facing_dot:       float = 0.55
    prefer_spawners:         bool  = True
    prefer_elites:           bool  = False
    pickup_radius:           float = 240.0
    max_seconds:             float = 90.0
    end_when_cleared:        bool  = True
    end_on_death:            bool  = True

    boss_score_bonus:      float = 60.0
    preferred_target_bonus: float = 250.0


@dataclass
class CombatIssue:
    severity: str
    code:     str
    message:  str
    data: dict = field(default_factory=dict)

    def to_dict(self):
        return dataclasses.asdict(self)


@dataclass
class CombatReport:
    seed:     int = 0
    scenario: dict = field(default_factory=dict)
    metrics:  dict = field(default_factory=dict)
    issues:   List[CombatIssue] = field(default_factory=list)
    log:      List[str] = field(default_factory=list)

    def add(self, sev, code, msg, **data):
        self.issues.append(CombatIssue(sev, code, msg, data))

    def score(self):
        return (sum(1 for i in self.issues if i.severity == FATAL),
                sum(1 for i in self.issues if i.severity == ERROR),
                sum(1 for i in self.issues if i.severity == WARN))

    def finalize(self):
        self.issues.sort(key=lambda i: _SEV.get(i.severity, 3))
        return self

    def to_dict(self):
        return {"seed": self.seed, "scenario": self.scenario,
                "metrics": self.metrics,
                "issues": [i.to_dict() for i in self.issues],
                "log":    self.log}


class CombatBot:

    def __init__(self, game: Game, cfg: Optional[CombatBotConfig] = None,
                 seed: int = 0, verbose: bool = False):
        self.game    = game
        self.cfg     = cfg or CombatBotConfig()
        self.rng     = random.Random(seed)
        self.verbose = verbose

        self.damage_dealt = 0
        self.damage_taken = 0
        self.attacks_issued = 0
        self.dashes_used = 0
        self.kills = 0
        self.deaths = 0
        self.loot_picked = 0
        self.preferred_target = None
        self._last_hp = game.player.effective_stats.hp
        game.event_bus.subscribe("player_death", self._on_death)

        self._hp_snapshot = {}     # id(target) -> hp at end of last tick
        self._alive_snapshot = 0   # alive target count at end of last tick
    

    def _on_death(self, **_):
        self.deaths += 1
        self._last_hp = 0


    def tick(self, dt: float):
        self._tick_modals()

        g, p = self.game, self.game.player

        # ---- Damage taken: player HP can be tracked within a tick. ----
        cur = p.effective_stats.hp
        if cur < self._last_hp:
            self.damage_taken += self._last_hp - cur
        self._last_hp = cur

        # ---- Damage dealt + kills: diff against the snapshot taken at the
        #      END of the previous tick.  Anything that resolved inside
        #      game._update() since then (projectiles, echoes, DOTs, chain
        #      procs, reactions, on-kill explosions, terrain damage) is
        #      captured here because the old snapshot is compared against
        #      the CURRENT hp values.
        #
        #      We iterate the previous snapshot's ids and hold a live
        #      reference to each target, so a target removed from
        #      area.enemies during _on_enemy_killed is still visible here
        #      (its hp was 0 and .alive flipped to False) and its killing
        #      blow is counted.  Fleeing/escaping targets are correctly
        #      excluded from both damage and kills.
        targets_now = {
            id(t): t
            for t in g.current_area.enemies + g.current_area.bosses
            if t.alive
        }

        for tid, (t_obj, prev_hp) in self._hp_snapshot.items():
            if getattr(t_obj, "_fled", False):
                # Removed from play while still alive (pack flee).
                # Not damage, not a kill.
                continue
            if t_obj.alive:
                cur_hp = t_obj.hp
            else:
                cur_hp = 0
                self.kills += 1
            if cur_hp < prev_hp:
                self.damage_dealt += prev_hp - cur_hp

        # Snapshot for the NEXT tick.
        self._hp_snapshot = {
            tid: (t, t.hp) for tid, t in targets_now.items()
        }

        if not p.alive:
            return

        threats = self._threats()
        loot    = self._nearby_loot()

        if threats and self._should_engage(threats):
            target = self._pick_target(threats)
            self._engage(target, threats)
        elif loot:
            self._walk_to_loot(loot)
        else:
            p.clear_move_target()

    

    def _tick_modals(self):
        g = self.game
        if g.choice_menu.visible:
            g.choice_menu._armed_at = 0
            try:
                g.choice_menu._pick(0)
            except Exception:
                g.choice_menu.visible = False
        if g.evolution_banner.visible:
            try:
                g._accept_pending_extension()
            except Exception:
                g.evolution_banner.dismiss()
        if g.gateway_menu.visible:
            g.gateway_menu.hide()
        if g.inventory_ui.visible:
            g.inventory_ui.toggle()

    def _threats(self):
        a, p = self.game.current_area, self.game.player
        out = []
        for e in a.enemies:
            if not e.alive:
                continue
            if abs(e.x - p.x) > 1200 or abs(e.y - p.y) > 1200:
                continue
            out.append(e)
        for b in a.bosses:
            if not b.alive or getattr(b, "dormant", False):
                continue
            if abs(b.x - p.x) > 1200 or abs(b.y - p.y) > 1200:
                continue
            out.append(b)
        return out

    def _nearby_loot(self):
        if self.game.player.inventory.is_full():
            return []
        a, p = self.game.current_area, self.game.player
        out = []
        for d in a.loot_drops:
            dx, dy = d["x"] - p.x, d["y"] - p.y
            dist = math.hypot(dx, dy)
            if dist <= self.cfg.pickup_radius:
                out.append((dist, d))
        out.sort(key=lambda t: t[0])
        return out

    def _should_engage(self, threats) -> bool:
        p = self.game.player
        frac = p.effective_stats.hp / max(1, p.effective_stats.max_hp)
        if frac < self.cfg.retreat_hp_frac:
            return False
        near = min(threats, key=lambda t: (t.x - p.x) ** 2 + (t.y - p.y) ** 2)
        return math.hypot(near.x - p.x, near.y - p.y) <= self.cfg.engage_range

    def _pick_target(self, threats):
        p = self.game.player
        scored = []
        for t in threats:
            d = math.hypot(t.x - p.x, t.y - p.y)
            s = d
            if getattr(t, "is_boss", False):
                s -= self.cfg.boss_score_bonus
            if t is self.preferred_target:
                s -= self.cfg.preferred_target_bonus
            if getattr(t, "is_elite", False) and self.cfg.prefer_elites:
                s -= 80
            if getattr(t, "is_spawner", False) and self.cfg.prefer_spawners:
                s -= 100
            scored.append((s, t))
        scored.sort(key=lambda kv: kv[0])
        return scored[0][1]

    def _engage(self, target, threats):
        g, p = self.game, self.game.player
        profile  = p.current_attack_profile()
        is_range = profile.kind == "projectile"

        if self._dodge_incoming(threats):
            return

        dist = math.hypot(target.x - p.x, target.y - p.y)

        if is_range:
            self._ranged_position(target, dist)
        else:
            self._melee_position(target, profile, dist)

        self._maybe_attack(target, profile, dist)

    def _dodge_incoming(self, threats) -> bool:
        if not self.cfg.dodge_telegraph:
            return False
        g, p = self.game, self.game.player

        for t in threats:
            tt = getattr(t, "_telegraph_timer", 0.0)
            tp = getattr(t, "_telegraph_pos", None)
            if tt > 0 and tp is not None:
                radius = 70 if getattr(t, "_telegraph_action", None) == "slam" else 85
                if math.hypot(p.x - tp[0], p.y - tp[1]) < radius + 16:
                    self._dash_dir(p.x - tp[0], p.y - tp[1])
                    self._move_away(tp[0], tp[1])
                    return True

            if getattr(t, "is_boss", False) and getattr(t, "_state", "") == "telegraph":
                action = getattr(t, "_pending_action", "melee")
                if action in ("melee", "dash", "ranged"):
                    px, py = self._perp(t.x, t.y, p.x, p.y)
                    self._dash_dir(px, py)
                    p.set_move_target(p.x + px * 140, p.y + py * 140)
                    return True
        return False

    @staticmethod
    def _perp(ax, ay, bx, by):
        dx, dy = bx - ax, by - ay
        d = math.hypot(dx, dy) or 1.0
        return -dy / d, dx / d

    def _dash_dir(self, dx, dy):
        p = self.game.player
        if self.cfg.dodge_requires_charge and p._dash_charges <= 0:
            return
        d = math.hypot(dx, dy) or 1.0
        p.facing = pygame.Vector2(dx / d, dy / d)
        if p.try_dash():
            self.dashes_used += 1

    def _move_away(self, x, y):
        p = self.game.player
        dx, dy = p.x - x, p.y - y
        d = math.hypot(dx, dy) or 1.0
        p.set_move_target(p.x + dx / d * 200, p.y + dy / d * 200)

    def _melee_position(self, target, profile, dist):
        p = self.game.player
        stick = profile.range * self.cfg.melee_stick_frac
        if dist > stick + 8:
            p.set_move_target(target.x, target.y)
        elif dist < stick * 0.55:
            dx, dy = p.x - target.x, p.y - target.y
            p.set_move_target(p.x + dx * 0.4, p.y + dy * 0.4)
        else:
            px, py = self._perp(p.x, p.y, target.x, target.y)
            p.set_move_target(p.x + px * 70, p.y + py * 70)
        p.face_toward(target.x, target.y)

    def _ranged_position(self, target, dist):
        p = self.game.player
        if dist < self.cfg.ranged_min:
            dx, dy = p.x - target.x, p.y - target.y
            d = math.hypot(dx, dy) or 1.0
            p.set_move_target(p.x + dx / d * 180, p.y + dy / d * 180)
        elif dist > self.cfg.ranged_max:
            p.set_move_target(target.x, target.y)
        else:
            px, py = self._perp(p.x, p.y, target.x, target.y)
            p.set_move_target(p.x + px * 70, p.y + py * 70)
        p.face_toward(target.x, target.y)

    def _maybe_attack(self, target, profile, dist):
        g, p = self.game, self.game.player
        if not p.can_attack():
            return
        p.face_toward(target.x, target.y)

        dx, dy = target.x - p.x, target.y - p.y
        d = math.hypot(dx, dy) or 1.0
        if p.facing.x * dx / d + p.facing.y * dy / d < self.cfg.attack_facing_dot:
            return
        if profile.kind == "arc" and dist > profile.range * 1.25:
            return

        g._try_attack()
        self.attacks_issued += 1

    def _walk_to_loot(self, loot):
        if not loot:
            return
        _, drop = loot[0]
        self.game.player.set_move_target(drop["x"], drop["y"])


def make_headless_game(seed: int, slot: str = "cb") -> tuple[Game, str]:
    tmp = tempfile.mkdtemp(prefix="arpg_cb_")
    config.SAVES_DIR = tmp
    game = Game(slot_id=slot, world_seed=seed)
    game.save_manager.save = lambda *a, **kw: None
    return game, tmp


def _place_player_in_room(game, room_name: str):
    level = game.starting_area
    room  = level.spec["rooms"][room_name]
    cx, cy = level._room_center(room)
    game.player.x, game.player.y = cx, cy
    game.camera.update(cx, cy)


def _clear_area(game):
    game.starting_area.enemies.clear()
    game.starting_area.bosses.clear()


def _spawn_wave(game, area_level: int, count: int, rng):
    from enemies.enemy import spawn_basic_enemy
    p = game.player
    for _ in range(count):
        for _try in range(40):
            ang = rng.uniform(0, math.tau)
            dist = rng.uniform(180, 280)
            x = p.x + math.cos(ang) * dist
            y = p.y + math.sin(ang) * dist
            r = pygame.Rect(int(x - 14), int(y - 14), 28, 28)
            if game.current_area.tilemap.collides_rect(r):
                continue
            e = spawn_basic_enemy(x, y, rng, area_level=area_level)
            e.region_id = "starting_area"
            e._pack_checked = True
            game.current_area.enemies.append(e)
            break


def _spawn_elite(game, area_level: int, rng):
    from enemies.enemy import spawn_basic_enemy
    p = game.player
    for _try in range(60):
        ang = rng.uniform(0, math.tau)
        dist = rng.uniform(220, 300)
        x = p.x + math.cos(ang) * dist
        y = p.y + math.sin(ang) * dist
        if game.current_area.tilemap.collides_rect(pygame.Rect(int(x-14), int(y-14), 28, 28)):
            continue
        e = spawn_basic_enemy(x, y, rng, area_level=area_level)
        e.is_elite = True
        e.max_hp = int(e.max_hp * 2.1)
        e.hp     = e.max_hp
        e.damage = int(e.damage * 1.7)
        e.name   = "Elite " + e.name
        e.region_id = "starting_area"
        e._pack_checked = True
        game.current_area.enemies.append(e)
        return e


def _spawn_boss(game, area_level: int, kind: str, rng):
    from enemies.boss import Boss, BossTemplate
    from enemies.boss_factory import build_personalized_boss_template
    p = game.player
    x, y = p.x + 220, p.y
    if kind == "hollow_warden":
        template = BossTemplate.hollow_warden()
    else:
        template = build_personalized_boss_template(
            name=f"Test Boss L{area_level}",
            boss_personalization={"mechanic_archetype": kind,
                                  "difficulty_bias": "normal"},
            area_level=area_level, rng=rng,
        )
    b = Boss(x, y, template)
    b.region_id = "starting_area"
    game.current_area.bosses.append(b)
    return b


def run_combat_scenario(seed: int, cfg: CombatBotConfig, scenario: dict,
                        verbose: bool = False) -> CombatReport:
    rep = CombatReport(seed=seed, scenario=scenario)
    tmp = None
    try:
        game, tmp = make_headless_game(seed)
        area_level = int(scenario.get("area_level", 5))
        rng = random.Random(seed ^ 0xA5A5)

        if scenario.get("start_level"):
            try:
                game._apply_test_level(int(scenario["start_level"]),
                                       force_weapon_subtype=scenario.get("force_weapon_subtype"),)
            except Exception as e:
                rep.add(WARN, "START_LEVEL_FAILED", str(e))
        game.player.heal_to_full()

        _clear_area(game)
        _place_player_in_room(game, "boss_room")

        setup = scenario.get("setup", "wave")
        if setup == "wave":
            _spawn_wave(game, area_level, count=int(scenario.get("count", 4)), rng=rng)
        elif setup == "elite":
            elite = _spawn_elite(game, area_level, rng=rng)
            if elite is None:
                rep.add(FATAL, "SCENARIO_SETUP_FAILED",
                        "could not place the requested elite")
                return rep.finalize()
        elif setup == "boss":
            _spawn_boss(game, area_level, kind=scenario.get("kind", "hollow_warden"), rng=rng)
        elif setup == "mixed":
            # A representative encounter: ordinary pressure, an elite priority
            # target, and a boss with its full mechanic/phase implementation.
            wave_count = max(2, min(8, int(scenario.get("count", 4)) - 2))
            _spawn_wave(game, area_level, count=wave_count, rng=rng)
            elite = _spawn_elite(game, area_level, rng=rng)
            if elite is None:
                rep.add(FATAL, "SCENARIO_SETUP_FAILED",
                        "could not place the elite in the mixed encounter")
                return rep.finalize()
            _spawn_boss(game, area_level,
                        kind=scenario.get("kind", "hollow_warden"), rng=rng)
        else:
            rep.add(FATAL, "BAD_SCENARIO", f"unknown setup '{setup}'")
            return rep.finalize()

        bot = CombatBot(game, cfg, seed=seed, verbose=verbose)

        dt = 1.0 / 60.0
        t = 0.0
        max_t = min(float(scenario.get("max_seconds", cfg.max_seconds)), 600.0)

        while t < max_t:
            if cfg.end_on_death and not game.player.alive:
                break
            bot.tick(dt)
            game._update(dt)
            t += dt

            if cfg.end_when_cleared:
                alive = sum(1 for e in game.current_area.enemies
                            if e.alive
                            and abs(e.x - game.player.x) < 1500
                            and abs(e.y - game.player.y) < 1500)
                alive_b = sum(1 for b in game.current_area.bosses
                              if b.alive and not getattr(b, "dormant", False))
                if alive == 0 and alive_b == 0:
                    break

        hp_frac = game.player.effective_stats.hp / max(1, game.player.effective_stats.max_hp)
        rep.metrics = {
            "time_elapsed":      round(t, 2),
            "player_alive":      bool(game.player.alive),
            "player_hp_frac":    round(hp_frac, 3),
            "damage_dealt":      bot.damage_dealt,
            "damage_taken":      bot.damage_taken,
            "attacks_issued":    bot.attacks_issued,
            "dashes_used":       bot.dashes_used,
            "kills":             bot.kills,
            "deaths":            bot.deaths,
            "loot_picked":       bot.loot_picked,
            "level":             game.player.progression.level,
            "enemies_remaining": sum(1 for e in game.current_area.enemies
                                     if e.alive
                                     and abs(e.x - game.player.x) < 1500
                                     and abs(e.y - game.player.y) < 1500),
            "bosses_remaining":  sum(1 for b in game.current_area.bosses
                                     if b.alive and not getattr(b, "dormant", False)),
        }
        if t > 0 and bot.damage_dealt > 0:
            rep.metrics["dps"] = round(bot.damage_dealt / t, 2)
        else:
            rep.metrics["dps"] = 0.0

        # A simulation that did not perform combat must never be counted as a
        # successful clear (even if its target lists happen to be empty).
        if bot.attacks_issued == 0 and bot.damage_dealt == 0:
            rep.add(ERROR, "NO_COMBAT_ACTIVITY",
                    "scenario ended without an attack or recorded damage",
                    elapsed=t, attacks=bot.attacks_issued)

        if not game.player.alive:
            rep.add(ERROR, "PLAYER_DIED", "bot died during scenario")
        if (rep.metrics["enemies_remaining"] + rep.metrics["bosses_remaining"]) and \
                cfg.end_when_cleared and t >= max_t:
            rep.add(WARN, "NOT_CLEARED", "scenario timed out with targets alive")
        if bot.damage_dealt == 0 and t > 3.0:
            rep.add(ERROR, "NO_DAMAGE", "bot dealt no damage in 3+ seconds")
        if bot.attacks_issued == 0 and t > 3.0:
            rep.add(ERROR, "NO_ATTACKS", "bot never issued an attack")
        return rep.finalize()

    except Exception as e:
        rep.add(FATAL, "BOT_CRASH", f"{type(e).__name__}: {e}",
                traceback=traceback.format_exc())
        return rep.finalize()
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def _derive_seed(base, name):
    import hashlib
    h = hashlib.sha256(f"{base}:{name}".encode()).digest()
    return int.from_bytes(h[:4], "big")


def run_one(seed: int, cfg: CombatBotConfig, scenario: dict,
            verbose: bool = False) -> CombatReport:
    return run_combat_scenario(seed, cfg, scenario, verbose=verbose)


def run_batch(count, base_seed, cfg, scenario, progress=True, verbose=False):
    seeds = [_derive_seed(base_seed, f"cb:{i}") for i in range(count)]
    results = []
    t0 = time.time()
    for i, s in enumerate(seeds):
        results.append(run_one(s, cfg, scenario, verbose=verbose))
        if progress and (i + 1) % 25 == 0:
            died = sum(1 for r in results if any(x.code == "PLAYER_DIED" for x in r.issues))
            print(f"\r  {i+1}/{count}   deaths: {died}", end="", flush=True)
    if progress:
        print()
    return results, time.time() - t0


def summarize(results: List[CombatReport], elapsed: float) -> dict:
    n = len(results)
    dead = sum(1 for r in results if any(i.code == "PLAYER_DIED" for i in r.issues))
    cleared = sum(1 for r in results
                  if not any(i.code in ("BAD_SCENARIO", "SCENARIO_SETUP_FAILED",
                                        "BOT_CRASH", "NO_COMBAT_ACTIVITY")
                             for i in r.issues)
                  and r.metrics.get("enemies_remaining", 0) == 0
                  and r.metrics.get("bosses_remaining", 0) == 0)
    valid = sum(1 for r in results
                if not any(i.code in ("BAD_SCENARIO", "SCENARIO_SETUP_FAILED",
                                      "BOT_CRASH", "NO_COMBAT_ACTIVITY")
                           for i in r.issues))

    dps_vals     = [r.metrics["dps"] for r in results if "dps" in r.metrics]
    taken_vals   = [r.metrics["damage_taken"] for r in results if "damage_taken" in r.metrics]
    time_vals    = [r.metrics["time_elapsed"] for r in results if "time_elapsed" in r.metrics]

    kill_vals = [r.metrics.get("kills", 0) for r in results if "kills" in r.metrics]

    def _p(xs, q):
        if not xs:
            return 0
        xs = sorted(xs)
        return xs[min(len(xs)-1, int(q*(len(xs)-1)))]

    code_maps = {}
    for r in results:
        for i in r.issues:
            key = (i.severity, i.code)
            code_maps[key] = code_maps.get(key, 0) + 1

    return {
        "scenarios":     n,
        "valid_scenarios": valid,
        "invalid_scenarios": n - valid,
        "cleared":       cleared,
        "died":          dead,
        "clear_rate":    cleared / n if n else 0.0,
        "death_rate":    dead / n if n else 0.0,
        "elapsed_s":     round(elapsed, 2),
        "dps":  {"mean": round(sum(dps_vals)/len(dps_vals), 2) if dps_vals else 0,
                 "p50":  _p(dps_vals, 0.5), "p95": _p(dps_vals, 0.95),
                 "max":  max(dps_vals, default=0)},
        "damage_taken": {"mean": round(sum(taken_vals)/len(taken_vals), 1) if taken_vals else 0,
                         "p50":  _p(taken_vals, 0.5), "p95": _p(taken_vals, 0.95)},
        "time_seconds": {"mean": round(sum(time_vals)/len(time_vals), 2) if time_vals else 0,
                         "p50":  _p(time_vals, 0.5), "p95": _p(time_vals, 0.95)},
        "issues": [
            {"severity": s, "code": c, "scenarios": k}
            for (s, c), k in sorted(code_maps.items(),
                                    key=lambda kv: (_SEV[kv[0][0]], -kv[1]))
        ],
        "kills": {"mean": round(sum(kill_vals) / len(kill_vals), 1) if kill_vals else 0,
          "max":  max(kill_vals, default=0)},
    }


def print_summary(s: dict):
    print(f"\n=== Combat bot: {s['scenarios']} scenarios in {s['elapsed_s']}s ===")
    print(f"Valid: {s.get('valid_scenarios', s['scenarios'])}/{s['scenarios']}  |  "
          f"Cleared: {s['cleared']}  |  Deaths: {s['died']}  "
          f"(clear {s['clear_rate']:.1%} / death {s['death_rate']:.1%})")
    print(f"DPS         mean {s['dps']['mean']:>6}   p50 {s['dps']['p50']:>6}   "
          f"p95 {s['dps']['p95']:>6}   max {s['dps']['max']}")
    print(f"Damage taken mean {s['damage_taken']['mean']:>6}   "
          f"p50 {s['damage_taken']['p50']:>6}   p95 {s['damage_taken']['p95']:>6}")
    print(f"Time (s)     mean {s['time_seconds']['mean']:>6}   "
          f"p50 {s['time_seconds']['p50']:>6}   p95 {s['time_seconds']['p95']:>6}")
    if s["issues"]:
        print("\nIssues:")
        for i in s["issues"]:
            print(f"  {i['severity']:<5} {i['code']:<22} {i['scenarios']:>5}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Level 2 combat bot")
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--seed",  type=int, default=1)
    ap.add_argument("--scenario", choices=["wave", "elite", "boss", "mixed", "custom"],
                    default="wave")
    ap.add_argument("--level",  type=int, default=5)
    ap.add_argument("--count-enemies", type=int, default=4,
                    help="wave size")
    ap.add_argument("--boss-kind", default="hollow_warden",
                    help="archetype: hollow_warden|dash_predator|ranged_zoner|summoner|reflector|berserker")
    ap.add_argument("--start-level", type=int, default=None,
                    help="grant full gear at this level (test convenience)")
    ap.add_argument("--max-seconds", type=float, default=90.0)
    ap.add_argument("--report", help="write JSON report here")
    ap.add_argument("--replay", type=int, default=None,
                    help="replay a single seed with verbose output")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    scenario = {
        "setup":       args.scenario if args.scenario != "custom" else "wave",
        "area_level":  args.level,
        "count":       args.count_enemies,
        "kind":        args.boss_kind,
        "max_seconds": args.max_seconds,
        "start_level": args.start_level,
    }

    cfg = CombatBotConfig(max_seconds=args.max_seconds)

    if args.replay is not None:
        rep = run_one(args.replay, cfg, scenario, verbose=True)
        print(json.dumps(rep.to_dict(), indent=2, default=str))
        return 0 if rep.score()[0] == 0 else 2

    results, elapsed = run_batch(args.count, args.seed, cfg, scenario,
                                 verbose=args.verbose)
    summary = summarize(results, elapsed)
    print_summary(summary)

    if args.report:
        with open(args.report, "w") as f:
            json.dump({"summary": summary,
                       "scenarios": [r.to_dict() for r in results]},
                      f, indent=1, default=str)
        print(f"\nJSON report -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
