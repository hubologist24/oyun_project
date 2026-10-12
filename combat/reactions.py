"""
Elemental reactions. Each hit applies ONE element aura to the target
(alternating through the weapon's elements / conversion targets). If a
different element is already on the target within the window, they react.
"""
import math
import pygame
from combat.damage import resolve_damage

REACTION_WINDOW = 3.0
REACTION_ICD = 0.8

REACTIONS = {
    frozenset(("fire", "cold")): "thermal_shock",       # big burst
    frozenset(("lightning", "cold")): "superconduct",   # armor shred nearby
    frozenset(("fire", "chaos")): "corruption",         # chaos DoT that spreads
    frozenset(("physical", "chaos")): "hemorrhage",     # bleed, stacks double
    frozenset(("physical", "bleed")): "hemorrhage",
    frozenset(("fire", "lightning")): "overload",       # AoE detonation
}
LABELS = {"thermal_shock": "THERMAL SHOCK", "superconduct": "SUPERCONDUCT",
          "corruption": "CORRUPTION", "hemorrhage": "HEMORRHAGE", "overload": "OVERLOAD"}
COLORS = {"thermal_shock": (255, 170, 120), "superconduct": (150, 220, 255),
          "corruption": (200, 90, 255), "hemorrhage": (230, 60, 70),
          "overload": (255, 240, 120)}


class ReactionSystem:
    def __init__(self, game):
        self.game = game
        self.reset()

    def reset(self):
        self.clock = 0.0
        self._auras = {}    # id(target) -> {"target","types","cd","n"}
        self._dots = []
        self._shreds = {}

    # ---------------- aura application ----------------
    def _entry(self, target):
        return self._auras.setdefault(id(target),
                                      {"target": target, "types": {}, "cd": 0.0, "n": 0})

    def _pick_element(self, player, target, world_rules):
        types = [player.effective_stats.primary_damage_type]
        weapon = player.equipped.get("weapon")
        if weapon:
            for a in weapon.all_affixes():
                if a.damage_type and a.damage_type not in types:
                    types.append(a.damage_type)
        if world_rules:
            for r in world_rules.conversion_rules_for("player"):
                if r.from_type in types and r.to_type not in types:
                    types.append(r.to_type)
        e = self._entry(target)
        t = types[e["n"] % len(types)]
        e["n"] += 1
        return t

    def on_hit(self, target, hit_damage, player, world_rules):
        dtype = self._pick_element(player, target, world_rules)
        e = self._entry(target)
        if self.clock < e["cd"]:
            return None
        e["types"][dtype] = self.clock + REACTION_WINDOW
        for other, expiry in list(e["types"].items()):
            if other == dtype or expiry < self.clock:
                continue
            name = REACTIONS.get(frozenset((dtype, other)))
            if name:
                e["types"].clear()
                e["cd"] = self.clock + REACTION_ICD
                self._fire(name, target, hit_damage)
                return name
        return None

    # ---------------- helpers ----------------
    def _nearby(self, target, radius):
        a = self.game.current_area
        return [o for o in list(a.enemies) + list(a.bosses)
                if o is not target and o.alive
                and math.hypot(o.x - target.x, o.y - target.y) <= radius]

    def _hurt(self, target, amount, dtype, color=None):
        from combat.combat import _enemy_category
        g = self.game
        if not target.alive:
            return
        dmg = resolve_damage(max(1, int(amount)), dtype, None,
                             target_resistances=target.resistances,
                             world_rules=g._active_world_rules(),
                             target_category=_enemy_category(target),
                             source_category="player")
        target.take_damage(dmg)
        g.damage_numbers.spawn(target.x, target.y - 30, str(dmg),
                               color or g._damage_type_color(dtype))
        if getattr(target, "phase_just_changed", False):
            target.phase_just_changed = False
            g._trigger_phase_transition(target)
        if not target.alive:
            if getattr(target, "is_boss", False):
                g._on_boss_killed(target)
            else:
                g._on_enemy_killed(target)

    def _add_dot(self, target, dtype, dps, duration, spread=False, stack=False):
        for d in self._dots:
            if d["target"] is target and d["type"] == dtype:
                d["left"] = duration
                d["dps"] = max(d["dps"], dps)
                if stack:
                    d["stacks"] = min(8, d["stacks"] * 2)   # bleed stacks double
                return
        self._dots.append({"target": target, "type": dtype, "dps": dps, "left": duration,
                           "acc": 0.0, "stacks": 1, "spread": spread, "age": 0.0})

    def _shred(self, o):
        s = self._shreds.get(id(o))
        if s:
            s["left"] = 4.0
            return
        armor = o.resistances.get("armor_flat", 0)
        phys = o.resistances.get("physical", 0.0)
        o.resistances["armor_flat"] = armor * 0.3
        o.resistances["physical"] = phys - 0.2
        self._shreds[id(o)] = {"target": o, "armor": armor, "phys": phys, "left": 4.0}

    # ---------------- reaction effects ----------------
    def _fire(self, name, target, hit):
        g = self.game
        color = COLORS[name]
        g.damage_numbers.spawn(target.x, target.y - 48, LABELS[name], color)
        g.crit_bursts.append({"x": target.x, "y": target.y, "radius": 70,
                              "life": 0.3, "max_life": 0.3, "color": color})
        base = max(4, hit)
        if name == "thermal_shock":
            self._hurt(target, base * 2.5, "fire", color)
        elif name == "superconduct":
            for o in [target] + self._nearby(target, 120):
                self._shred(o)
        elif name == "corruption":
            self._add_dot(target, "chaos", base * 0.5, 4.0, spread=True)
        elif name == "hemorrhage":
            self._add_dot(target, "bleed", base * 0.4, 4.0, stack=True)
        elif name == "overload":
            for o in self._nearby(target, 100):
                self._hurt(o, base * 0.9, "lightning", color)
            self._hurt(target, base * 0.9, "lightning", color)

    # ---------------- per-frame ----------------
    def tick(self, dt):
        self.clock += dt
        for d in list(self._dots):
            t = d["target"]
            d["left"] -= dt
            d["acc"] += dt
            d["age"] += dt
            if not t.alive or d["left"] <= 0:
                self._dots.remove(d)
                continue
            if d["spread"] and d["age"] >= 1.0:
                d["spread"] = False
                for o in self._nearby(t, 90):
                    self._add_dot(o, d["type"], d["dps"] * 0.7, d["left"])
            if d["acc"] >= 0.5:
                d["acc"] -= 0.5
                self._hurt(t, d["dps"] * 0.5 * d["stacks"], d["type"])
        for k, s in list(self._shreds.items()):
            s["left"] -= dt
            if s["left"] <= 0 or not s["target"].alive:
                s["target"].resistances["armor_flat"] = s["armor"]
                s["target"].resistances["physical"] = s["phys"]
                del self._shreds[k]
        for k, e in list(self._auras.items()):
            live = any(v > self.clock for v in e["types"].values())
            if not e["target"].alive or (not live and self.clock >= e["cd"]):
                del self._auras[k]

    def draw(self, surface, camera):
        for e in self._auras.values():
            t = e["target"]
            if not t.alive:
                continue
            live = [k for k, v in e["types"].items() if v > self.clock]
            for i, k in enumerate(live):
                off = (i - (len(live) - 1) / 2) * 10
                p = camera.world_to_screen((t.x + off, t.y - t.height / 2 - 14))
                pygame.draw.circle(surface, self.game._damage_type_color(k), p, 4)
                pygame.draw.circle(surface, (0, 0, 0), p, 4, 1)