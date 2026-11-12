# [PROJECT: tools] [ENTRY POINT]
# [SYMBOLS] classes: _NoKeys, AdventureConfig, AdventureEvent, AdventureIssue, AdventureReport, AdventureBot | functions: make_headless_game, run_adventure, _derive_seed, run_one, run_batch, summarize, print_summary, main
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
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame                                                    # noqa: E402

import core.audio as _audio                                      # noqa: E402
_audio.init = lambda: None                                       # noqa: E731
_audio.play = lambda *a, **kw: None                              # noqa: E731

class _NoKeys:
    def __getitem__(self, k): return False
    def __iter__(self):       return iter(())
    def __len__(self):        return 0

pygame.key.get_pressed = lambda: _NoKeys()                       # noqa: E731
pygame.mouse.get_pos   = lambda: (0, 0)                          # noqa: E731

import core.config as config                                     # noqa: E402
from core.game import Game                                       # noqa: E402
from tools.combat_bot import CombatBot, CombatBotConfig          # noqa: E402


FATAL, ERROR, WARN = "FATAL", "ERROR", "WARN"
_SEV = {FATAL: 0, ERROR: 1, WARN: 2}


@dataclass
class AdventureConfig:
    combat_cfg:       CombatBotConfig = field(default_factory=CombatBotConfig)
    extend_free:      bool  = True
    auto_equip:       bool  = True
    equip_margin:     float = 6.0
    max_extensions:   int   = 6
    max_time_seconds: float = 300.0
    start_level:      Optional[int] = None
    force_weapon_subtype: Optional[str] = None          # <-- NEW

    god_stones:        bool = False
    bypass_seals:      bool = False
    bypass_level_req:  bool = False

    boss_hp_scale:     float = 1.0
    boss_damage_scale: float = 1.0
    tutorial_boss_hp:  Optional[int] = None


@dataclass
class AdventureEvent:
    t: float
    kind: str
    detail: dict = field(default_factory=dict)


@dataclass
class AdventureIssue:
    severity: str
    code:     str
    message:  str
    data: dict = field(default_factory=dict)

    def to_dict(self):
        return dataclasses.asdict(self)


@dataclass
class AdventureReport:
    seed: int = 0
    metrics: dict = field(default_factory=dict)
    issues: List[AdventureIssue] = field(default_factory=list)
    events: List[AdventureEvent] = field(default_factory=list)

    def add(self, sev, code, msg, **data):
        self.issues.append(AdventureIssue(sev, code, msg, data))

    def log(self, t, kind, **detail):
        self.events.append(AdventureEvent(t=round(t, 2), kind=kind, detail=detail))

    def score(self):
        return (sum(1 for i in self.issues if i.severity == FATAL),
                sum(1 for i in self.issues if i.severity == ERROR),
                sum(1 for i in self.issues if i.severity == WARN))

    def finalize(self):
        self.issues.sort(key=lambda i: _SEV.get(i.severity, 3))
        return self

    def to_dict(self):
        return {"seed": self.seed, "metrics": self.metrics,
                "issues": [i.to_dict() for i in self.issues],
                "events": [dataclasses.asdict(e) for e in self.events]}


class AdventureBot:
    def __init__(self, game: Game, cfg: AdventureConfig,
                 seed: int = 0, verbose: bool = False):
        self.game    = game
        self.cfg     = cfg
        self.combat  = CombatBot(game, cfg.combat_cfg, seed=seed, verbose=verbose)
        self.rng     = random.Random(seed ^ 0xA1CE)
        self.verbose = verbose

        self.clock = 0.0
        self.events: List[AdventureEvent] = []

        self._path: Optional[List[Tuple[int, int]]] = None
        self._path_target: Optional[Tuple[int, int]] = None
        self._path_timer = 0.0
        self._stuck_pos = (game.player.x, game.player.y)
        self._stuck_timer = 0.0

        self.equipped_count = 0
        self.extensions_entered: List[str] = []
        self.stones_spent = 0
        self.stones_earned = 0
        self.auto_equip_interval = 4.0
        self._equip_timer = 0.0

        self._scaled_bosses: set[int] = set()
        self._setup_bypasses()
        self._stones_prev = game.player.extension_stones

        self._shrine_cd = 0.0                 # NEW — shrine interaction cooldown
        self._extend_urgency_timer = 0.0      # NEW — force-extend timer
        self.extend_urgency_seconds = 25.0    # NEW — how long before we *force* one


    def _setup_bypasses(self):
        g = self.game
        if self.cfg.god_stones:
            g.player.extension_stones = 999_999
        for gate in g.world.gates:
            if self.cfg.bypass_seals and gate.seal:
                gate.seal["ready"] = True
            if self.cfg.bypass_level_req:
                gate.level_requirement = 0


    def _apply_pending_scaling(self):
        for b in self.game.current_area.bosses:
            bid = id(b)
            if bid in self._scaled_bosses:
                continue
            if getattr(b, "dormant", False):
                continue
            if getattr(b, "is_nemesis", False):
                continue
            self._scaled_bosses.add(bid)

            if (self.cfg.tutorial_boss_hp is not None
                    and getattr(b, "region_id", None) == "starting_area"):
                b.max_hp = self.cfg.tutorial_boss_hp
                b.hp = b.max_hp
            else:
                hp_s = self.cfg.boss_hp_scale
                dmg_s = self.cfg.boss_damage_scale
                if hp_s != 1.0:
                    b.max_hp = max(1, int(b.max_hp * hp_s))
                    b.hp = b.max_hp
                if dmg_s != 1.0:
                    import dataclasses as _dc
                    b.template = _dc.replace(
                        b.template,
                        melee_damage=max(1, int(b.template.melee_damage * dmg_s)),
                        ranged_damage=max(1, int(b.template.ranged_damage * dmg_s)),
                        dash_damage=max(1, int(b.template.dash_damage * dmg_s)),
                    )

    def tick(self, dt: float) -> None:

        cur = self.game.player.extension_stones
        gain = cur - self._stones_prev
        if gain > 0:
            self.stones_earned += gain
        self._stones_prev = cur
                    

        self.clock += dt
        self._tick_modals()
        

        g, p = self.game, self.game.player
        if not p.alive:
            return

        # ---- NEW: force-extend before combat priority ----
        self._extend_urgency_timer += dt
        if self._extend_urgency_timer >= self.extend_urgency_seconds:
            self._extend_urgency_timer = 0.0
            if self._should_extend():
                self._extend_world()
                return
        # --------------------------------------------------

        

        if self.cfg.auto_equip:
            self._equip_timer += dt
            if self._equip_timer >= self.auto_equip_interval:
                self._equip_timer = 0.0
                self._auto_equip()

        hp_frac = p.effective_stats.hp / max(1, p.effective_stats.max_hp)
        threats = self.combat._threats()
        boss    = self._region_boss()

        # Drive the actual gate objective instead of wandering indefinitely.
        # In particular, the starting gate is commonly guarded by a boss; the
        # previous policy only noticed that boss after it was already active
        # and nearby, so the agent could spend its entire time farming mobs.
        objective_gate = self._unresolved_gate()
        seal = (getattr(objective_gate, "seal", None) or {})
        if seal.get("type") == "boss":
            guardian = self._guardian_for_gate(objective_gate)
            if guardian is not None:
                self.combat.preferred_target = guardian
                guardian_dist = math.hypot(guardian.x - p.x, guardian.y - p.y)
                if guardian_dist > self.combat.cfg.engage_range:
                    self._travel_to(guardian.x, guardian.y, dt)
                    return
                if getattr(guardian, "dormant", False):
                    self._travel_to(guardian.x, guardian.y, dt)
                    return
                # Keep the guardian as the explicit target even when ordinary
                # enemies are closer; CombatBot's target scoring honors it.
                threats = self.combat._threats()
        else:
            self.combat.preferred_target = boss

        nearest_threat = (
            min(math.hypot(t.x - p.x, t.y - p.y) for t in threats)
            if threats else float("inf")
        )

        self._shrine_cd = max(0.0, self._shrine_cd - dt)
        if nearest_threat > 200.0 and hp_frac < 0.95 and self._shrine_cd <= 0:
            for s in g.current_area.shrines:
                if s.area_id != g.current_area_id:
                    continue
                if not s.usable:
                    continue
                if s.player_in_range(p):
                    self._shrine_cd = 4.0
                    try:
                        msg = s.interact(g)
                        if msg:
                            self._note("shrine_used", shrine=s.def_.id,
                                       msg=msg[:80])
                    except Exception as e:
                        self._note("shrine_error", shrine=s.def_.id, err=str(e))
                    return

        if threats and hp_frac < self.combat.cfg.retreat_hp_frac:
            orb = self._find_heal_orb(p)
            if orb is not None:
                self._travel_to(orb["x"], orb["y"], dt)
                return
            near = min(threats, key=lambda t: (t.x - p.x) ** 2 + (t.y - p.y) ** 2)
            dx, dy = p.x - near.x, p.y - near.y
            d = math.hypot(dx, dy) or 1.0
            self._travel_to(p.x + dx / d * 300, p.y + dy / d * 300, dt)
            return

        if threats:
            target = self.combat._pick_target(threats)
            dist   = math.hypot(target.x - p.x, target.y - p.y)
            profile = p.current_attack_profile()

            if profile.kind == "arc":
                weapon_reach = profile.range * 2.0
            else:
                weapon_reach = self.combat.cfg.ranged_max

            if dist <= weapon_reach:
                self.combat.tick(dt)
                return

            self._travel_to(target.x, target.y, dt)
            return

        loot = self.combat._nearby_loot()
        if loot:
            self.combat.tick(dt)
            return

        if self._should_extend():
            self._extend_world()
            return

        shrine_obj = self._next_shrine_target()
        if shrine_obj is not None:
            self._travel_to(shrine_obj.x, shrine_obj.y, dt)
            return

        # Proximity-driven seals (survival, trial, bargain and corruption)
        # only progress while the player is near their gate. Park at a safe
        # interaction radius so the real game seal code can advance them.
        if objective_gate is not None and seal.get("type") in {
                "survival", "trial", "bargain", "corruption"}:
            gx = (objective_gate.tile_col + 0.5) * config.TILE_SIZE
            gy = (objective_gate.tile_row + 0.5) * config.TILE_SIZE
            if math.hypot(gx - p.x, gy - p.y) > 135.0:
                self._travel_to(gx, gy, dt)
            else:
                p.clear_move_target()
            return

        self._wander(dt)

    def _unresolved_gate(self):
        g = self.game
        gates = [
            gate for gate in g.world.gates_in_area(g.current_area_id)
            if gate.state == "closed"
            and (getattr(gate, "seal", None) or {}).get("ready") is not True
            and gate.level_requirement <= g.player.progression.level
        ]
        return gates[0] if gates else None

    def _guardian_for_gate(self, gate):
        if gate is None:
            return None
        target_name = (getattr(gate, "seal", None) or {}).get("target_boss")
        for boss in self.game.current_area.bosses:
            if not boss.alive or getattr(boss, "is_nemesis", False):
                continue
            if getattr(boss, "region_id", self.game.current_area_id) != gate.owner_area_id:
                continue
            if target_name and boss.name != target_name:
                continue
            return boss
        return None

    def _find_heal_orb(self, p) -> Optional[dict]:
        best, best_d = None, 400.0
        for d in self.game.current_area.loot_drops:
            if d.get("kind") != "heal":
                continue
            dist = math.hypot(d["x"] - p.x, d["y"] - p.y)
            if dist < best_d:
                best_d, best = dist, d
        return best

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

    def _note(self, kind: str, **detail):
        self.events.append(AdventureEvent(t=round(self.clock, 2),
                                          kind=kind, detail=detail))
        if self.verbose:
            print(f"[{self.clock:6.1f}s] {kind} {detail}")

    def _region_boss(self):
        self._apply_pending_scaling()
        g = self.game
        for b in g.current_area.bosses:
            if not b.alive:
                continue
            if getattr(b, "dormant", False):
                continue
            if getattr(b, "region_id", None) != g.current_area_id:
                continue
            return b
        return None

    def _next_shrine_target(self):
        g = self.game
        for gate in g.world.gates_in_area(g.current_area_id):
            seal = getattr(gate, "seal", None) or {}
            if seal.get("type") == "shrines" and not seal.get("ready"):
                for s in g.current_area.shrines:
                    if s.area_id == g.current_area_id and s.usable:
                        return s
        return None

    def _find_eligible_gate(self):
        g = self.game
        for gate in g.world.gates_in_area(g.current_area_id):
            if gate.state != "closed":
                continue
            if not g._gate_unlocked(gate):
                continue
            return gate
        return None

    def _should_extend(self) -> bool:
        g = self.game
        if len(g.world.extensions) >= self.cfg.max_extensions:
            return False
        gate = self._find_eligible_gate()
        if gate is None:
            return False
        if not self.cfg.extend_free and g.player.extension_stones < 1:
            return False
        return True

    def _extend_world(self):
        g = self.game
        gate = self._find_eligible_gate()
        if gate is None:
            return
        mode = (config.EXTENSION_MODE_DEBUG if self.cfg.extend_free
                else config.EXTENSION_MODE_MANUAL)
        stones_before = g.player.extension_stones

        try:
            g._begin_extension_for_gate(gate, mode)
        except Exception as e:
            self._note("extend_begin_failed", err=str(e))
            return
        if g._pending_extension_spec is None:
            return

        try:
            g._accept_pending_extension()
        except Exception as e:
            self._note("extend_accept_failed", err=str(e))
            g._pending_extension_spec = None
            g._pending_gate = None
            g.evolution_banner.dismiss()
            return

        stones_after = g.player.extension_stones
        if stones_before > stones_after:
            self.stones_spent += (stones_before - stones_after)

        rid = gate.target_extension_id
        if rid:
            try:
                g._teleport_to_region(rid)
                self.extensions_entered.append(rid)
                self._note("entered_extension", rid=rid,
                           ext_count=len(g.world.extensions),
                           area_level=getattr(g.world.get_extension(rid).spec,
                                              "area_level", 0))
            except Exception as e:
                self._note("teleport_failed", rid=rid, err=str(e))
        self._path = None
        self._path_target = None

    def _auto_equip(self):
        if not self.cfg.auto_equip:
            return
        g, p = self.game, self.game.player
        from items.equipment_slots import ITEM_SLOT_TO_EQUIP_SLOTS
        from items.comparison import compare_items

        for item in list(p.inventory.items):
            cands = ITEM_SLOT_TO_EQUIP_SLOTS.get(item.slot, [])
            if not cands:
                continue
            ok, _ = p.meets_requirements(item)
            if not ok:
                continue

            best_slot = None
            best_net  = 0.0
            for slot in cands:
                cur = p.equipped.get(slot)
                diffs = compare_items(item, cur)
                gain = sum(d["delta"] for d in diffs if d["is_positive"])
                loss = sum(-d["delta"] for d in diffs if not d["is_positive"])
                net  = gain - loss
                if net > best_net:
                    best_net  = net
                    best_slot = slot
            if best_slot and best_net >= self.cfg.equip_margin:
                if p.equip(item, slot=best_slot):
                    self.equipped_count += 1
                    self._note("equipped", item=item.display_name,
                               slot=best_slot, net=round(best_net, 1))

    def _travel_to(self, x: float, y: float, dt: float):
        p = self.game.player
        ts = config.TILE_SIZE
        tx, ty = int(x // ts), int(y // ts)
        self._path_timer += dt

        if (self._path is None
                or self._path_target != (tx, ty)
                or self._path_timer >= 1.0):
            self._path = self._bfs_path((int(p.x // ts), int(p.y // ts)), (tx, ty))
            self._path_target = (tx, ty)
            self._path_timer = 0.0

        if not self._path:
            p.set_move_target(x, y)
            self._check_stuck(dt)
            return

        pcx, pcy = int(p.x // ts), int(p.y // ts)
        while len(self._path) > 1 and self._path[0] == (pcx, pcy):
            self._path.pop(0)

        look = min(3, len(self._path) - 1)
        wx, wy = self._path[look]
        p.set_move_target(wx * ts + ts / 2, wy * ts + ts / 2)

        if look == 0 and math.hypot(p.x - x, p.y - y) < ts * 1.2:
            p.clear_move_target()
        self._check_stuck(dt)

    def _check_stuck(self, dt: float):
        p = self.game.player
        if math.hypot(p.x - self._stuck_pos[0], p.y - self._stuck_pos[1]) > 4.0:
            self._stuck_pos = (p.x, p.y)
            self._stuck_timer = 0.0
            return
        self._stuck_timer += dt
        if self._stuck_timer > 2.0:
            self._path = None
            self._path_target = None
            self._stuck_timer = 0.0
            rng = self.rng
            ts = config.TILE_SIZE
            grid = self.game.current_area.tilemap.grid
            for _ in range(20):
                cx = int(p.x // ts) + rng.randint(-4, 4)
                cy = int(p.y // ts) + rng.randint(-4, 4)
                if 0 <= cy < len(grid) and 0 <= cx < len(grid[0]) and grid[cy][cx] == 0:
                    p.set_move_target(cx * ts + ts / 2, cy * ts + ts / 2)
                    return

    def _bfs_path(self, start, goal) -> Optional[List[Tuple[int, int]]]:
        grid = self.game.current_area.tilemap.grid
        rows, cols = len(grid), len(grid[0])
        if not (0 <= start[1] < rows and 0 <= start[0] < cols):
            return None
        if not (0 <= goal[1] < rows and 0 <= goal[0] < cols):
            return None

        if grid[goal[1]][goal[0]] != 0:
            for radius in (1, 2, 3):
                for dy in range(-radius, radius + 1):
                    for dx in range(-radius, radius + 1):
                        ny, nx = goal[1] + dy, goal[0] + dx
                        if 0 <= ny < rows and 0 <= nx < cols and grid[ny][nx] == 0:
                            goal = (nx, ny)
                            break
                    else:
                        continue
                    break
                else:
                    continue
                break
            else:
                return None

        q = deque([start])
        came: dict = {start: None}
        max_iter = 50000
        while q and max_iter > 0:
            cur = q.popleft()
            max_iter -= 1
            if cur == goal:
                path = []
                c = cur
                while c is not None:
                    path.append(c)
                    c = came[c]
                path.reverse()
                return path
            cx, cy = cur
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (cx + dx, cy + dy)
                if nxt in came:
                    continue
                nx, ny = nxt
                if not (0 <= ny < rows and 0 <= nx < cols):
                    continue
                if grid[ny][nx] != 0:
                    continue
                came[nxt] = cur
                q.append(nxt)
        return None

    def _wander(self, dt: float):
        g, p = self.game, self.game.player
        self._path_timer += dt
        if self._path is None or self._path_timer >= 4.0:
            ts = config.TILE_SIZE
            grid = g.current_area.tilemap.grid
            px, py = int(p.x // ts), int(p.y // ts)
            for _ in range(40):
                tx = px + self.rng.randint(-18, 18)
                ty = py + self.rng.randint(-12, 12)
                if 0 <= ty < len(grid) and 0 <= tx < len(grid[0]) and grid[ty][tx] == 0:
                    self._path = self._bfs_path((px, py), (tx, ty))
                    self._path_target = (tx, ty)
                    self._path_timer = 0.0
                    break

        if self._path and len(self._path) > 1:
            ts = config.TILE_SIZE
            look = min(3, len(self._path) - 1)
            wx, wy = self._path[look]
            p.set_move_target(wx * ts + ts / 2, wy * ts + ts / 2)
            pcx, pcy = int(p.x // ts), int(p.y // ts)
            while len(self._path) > 1 and self._path[0] == (pcx, pcy):
                self._path.pop(0)
            self._check_stuck(dt)


def make_headless_game(seed: int, slot: str) -> tuple[Game, str]:
    tmp = tempfile.mkdtemp(prefix="arpg_ab_")
    config.SAVES_DIR = tmp
    game = Game(slot_id=slot, world_seed=seed)
    game.save_manager.save = lambda *a, **kw: None
    return game, tmp


def run_adventure(seed: int, cfg: AdventureConfig, verbose: bool = False) -> AdventureReport:
    rep = AdventureReport(seed=seed)
    tmp = None
    try:
        game, tmp = make_headless_game(seed, f"ab{seed & 0xffff}")
        if cfg.start_level:
            try:
                game._apply_test_level(int(cfg.start_level),
                                       force_weapon_subtype=cfg.force_weapon_subtype, )
            except Exception as e:
                rep.add(WARN, "START_LEVEL_FAILED", str(e))
        game.player.heal_to_full()

        bot = AdventureBot(game, cfg, seed=seed, verbose=verbose)
        dt = 1.0 / 60.0
        t  = 0.0
        max_t = float(cfg.max_time_seconds)

        while t < max_t:
            if len(game.world.extensions) >= cfg.max_extensions:
                break
            bot.tick(dt)
            game._update(dt)
            t += dt

        rep.metrics = {
            "time_elapsed":       round(t, 2),
            "extensions":         len(game.world.extensions),
            "level":              game.player.progression.level,
            "player_alive":       bool(game.player.alive),
            "hp_frac":            round(game.player.effective_stats.hp /
                                        max(1, game.player.effective_stats.max_hp), 3),
            "player_deaths":      bot.combat.deaths,
            "kills":              bot.combat.kills,
            "damage_dealt":       bot.combat.damage_dealt,
            "damage_taken":       bot.combat.damage_taken,
            "stones_earned":      bot.stones_earned,
            "stones_spent":       bot.stones_spent,
            "items_equipped":     bot.equipped_count,
            "extensions_entered": list(bot.extensions_entered),
            "regions_cleared":    len(game.world.cleared_regions),
            "gates_opened":       sum(1 for g in game.world.gates if g.state == "open"),
        }

        if t >= max_t - dt:
            rep.add(WARN, "TIMEOUT", f"reached {max_t:.0f}s without finishing")
        if cfg.max_extensions >= 1 and len(game.world.extensions) == 0:
            rep.add(ERROR, "NO_EXTENSIONS", "bot never generated an extension")
        if bot.extensions_entered and len(bot.extensions_entered) < len(game.world.extensions):
            rep.add(WARN, "NOT_ALL_ENTERED",
                    f"entered {len(bot.extensions_entered)}/{len(game.world.extensions)}")

        rep.events = bot.events
        return rep.finalize()

    except Exception as e:
        rep.add(FATAL, "BOT_CRASH", f"{type(e).__name__}: {e}",
                traceback=traceback.format_exc())
        return rep.finalize()
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def _derive_seed(base: int, name: str) -> int:
    import hashlib
    h = hashlib.sha256(f"{base}:{name}".encode()).digest()
    return int.from_bytes(h[:4], "big")


def run_one(seed, cfg, verbose=False) -> AdventureReport:
    return run_adventure(seed, cfg, verbose=verbose)


def run_batch(count, base_seed, cfg, verbose=False):
    seeds = [_derive_seed(base_seed, f"ab:{i}") for i in range(count)]
    results = []
    t0 = time.time()
    for i, s in enumerate(seeds):
        print(f"\r  {i+1}/{count}...", end="", flush=True)
        results.append(run_one(s, cfg, verbose=verbose))
    print()
    return results, time.time() - t0


def summarize(results: List[AdventureReport], elapsed: float) -> dict:
    n = len(results)

    def _mean(xs):
        return round(sum(xs)/len(xs), 2) if xs else 0
    def _p(xs, q):
        if not xs:
            return 0
        xs = sorted(xs)
        return xs[min(len(xs)-1, int(q*(len(xs)-1)))]

    exts  = [r.metrics["extensions"]     for r in results]
    lvls  = [r.metrics["level"]          for r in results]
    time  = [r.metrics["time_elapsed"]   for r in results]
    kills = [r.metrics["kills"]          for r in results]
    dmg_d = [r.metrics["damage_dealt"]   for r in results]
    dmg_t = [r.metrics["damage_taken"]   for r in results]
    deaths = [r.metrics["player_deaths"] for r in results]
    earned = [r.metrics["stones_earned"] for r in results]

    code_maps = {}
    for r in results:
        for i in r.issues:
            code_maps[(i.severity, i.code)] = code_maps.get((i.severity, i.code), 0) + 1

    return {
        "runs":        n,
        "elapsed_s":   round(elapsed, 2),
        "extensions":  {"mean": _mean(exts), "p50": _p(exts, 0.5), "max": max(exts, default=0)},
        "level":       {"mean": _mean(lvls), "p50": _p(lvls, 0.5), "max": max(lvls, default=0)},
        "time_seconds": {"mean": _mean(time), "p50": _p(time, 0.5),
                         "p95": _p(time, 0.95), "max": max(time, default=0)},
        "kills":       {"mean": _mean(kills), "p50": _p(kills, 0.5)},
        "damage_dealt": {"mean": _mean(dmg_d), "p50": _p(dmg_d, 0.5)},
        "damage_taken": {"mean": _mean(dmg_t), "p50": _p(dmg_t, 0.5)},
        "deaths":      {"mean": _mean(deaths), "max": max(deaths, default=0)},
        "stones_earned": {"mean": _mean(earned), "max": max(earned, default=0)},
        "issues": [
            {"severity": s, "code": c, "runs": k}
            for (s, c), k in sorted(code_maps.items(),
                                    key=lambda kv: (_SEV[kv[0][0]], -kv[1]))
        ],
    }


def print_summary(s: dict):
    print(f"\n=== Adventure bot: {s['runs']} runs in {s['elapsed_s']}s ===")
    print(f"Extensions  mean {s['extensions']['mean']:>5}   "
          f"p50 {s['extensions']['p50']:>5}   max {s['extensions']['max']}")
    print(f"Level       mean {s['level']['mean']:>5}   "
          f"p50 {s['level']['p50']:>5}   max {s['level']['max']}")
    print(f"Time (s)    mean {s['time_seconds']['mean']:>5}   "
          f"p50 {s['time_seconds']['p50']:>5}   "
          f"p95 {s['time_seconds']['p95']:>5}   max {s['time_seconds']['max']}")
    print(f"Kills       mean {s['kills']['mean']:>5}   "
          f"Deaths mean {s['deaths']['mean']:>5}  max {s['deaths']['max']}")
    print(f"Dmg dealt   mean {s['damage_dealt']['mean']:>9}")
    print(f"Dmg taken   mean {s['damage_taken']['mean']:>9}")
    print(f"Stones      mean {s['stones_earned']['mean']:>5}   max {s['stones_earned']['max']}")
    if s["issues"]:
        print("\nIssues:")
        for i in s["issues"]:
            print(f"  {i['severity']:<5} {i['code']:<22} {i['runs']:>5}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Level 3 adventure bot")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-extensions", type=int, default=5)
    ap.add_argument("--max-time", type=float, default=300.0)
    ap.add_argument("--start-level", type=int, default=None)
    ap.add_argument("--force-weapon-subtype", default=None,
                    choices=["sword", "greatsword", "bow",
                             "short_wand", "long_wand", "gun"])
    ap.add_argument("--no-extend-free", action="store_true",
                    help="spend Extension Stones to extend (test economy)")
    ap.add_argument("--no-auto-equip", action="store_true")
    ap.add_argument("--report", help="write JSON report here")
    ap.add_argument("--replay", type=int, default=None)
    ap.add_argument("--verbose", action="store_true")

    ap.add_argument("--god-stones", action="store_true",
                    help="~1M Extension Stones (bypass economy)")
    ap.add_argument("--bypass-seals", action="store_true",
                    help="Force all gate seals to ready")
    ap.add_argument("--bypass-level-req", action="store_true",
                    help="Ignore gate.level_requirement")
    ap.add_argument("--boss-hp-scale", type=float, default=1.0)
    ap.add_argument("--boss-damage-scale", type=float, default=1.0)
    ap.add_argument("--tutorial-boss-hp", type=int, default=None,
                    help="Set starting-area boss to exact HP (overrides --boss-hp-scale)")

    args = ap.parse_args(argv)

    cfg = AdventureConfig(
        extend_free       = not args.no_extend_free,
        auto_equip        = not args.no_auto_equip,
        max_extensions    = args.max_extensions,
        max_time_seconds  = args.max_time,
        start_level       = args.start_level,
        force_weapon_subtype=args.force_weapon_subtype,
        god_stones        = args.god_stones,
        bypass_seals      = args.bypass_seals,
        bypass_level_req  = args.bypass_level_req,
        boss_hp_scale     = args.boss_hp_scale,
        boss_damage_scale = args.boss_damage_scale,
        tutorial_boss_hp  = args.tutorial_boss_hp,
    )

    if args.replay is not None:
        rep = run_one(args.replay, cfg, verbose=True)
        print(json.dumps(rep.to_dict(), indent=2, default=str))
        return 0

    results, elapsed = run_batch(args.runs, args.seed, cfg, verbose=args.verbose)
    summary = summarize(results, elapsed)
    print_summary(summary)

    if args.report:
        with open(args.report, "w") as f:
            json.dump({"summary": summary,
                       "runs": [r.to_dict() for r in results]},
                      f, indent=1, default=str)
        print(f"\nJSON report -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
