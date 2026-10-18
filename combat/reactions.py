"""
Elemental reactions with weapon-specific delivery routes.

Every weapon subtype now delivers elements differently:
  sword       - hits 1-2 mark with the primary element, hit 3 applies the secondary
  greatsword  - every hit applies ALL elements at once (the sweep releases them)
  bow         - standing still: primary element, moving: secondary element
  short_wand  - fast alternation through all elements
  long_wand   - each hit also drops a short-lived zone applying the other element
Heavy Reversal "exposes" a target: its next hit is guaranteed to react.
"""
import math
import pygame
from combat.damage import resolve_damage

REACTION_WINDOW = 3.0
REACTION_ICD = 0.8

REACTIONS = {
    frozenset(("fire", "cold")): "thermal_shock",
    frozenset(("lightning", "cold")): "superconduct",
    frozenset(("fire", "chaos")): "corruption",
    frozenset(("physical", "chaos")): "hemorrhage",
    frozenset(("physical", "bleed")): "hemorrhage",
    frozenset(("fire", "lightning")): "overload",
    # new
    frozenset(("cold", "chaos")): "entropy_frost",
    frozenset(("lightning", "physical")): "magnetic_rupture",
    frozenset(("cold", "bleed")): "crimson_ice",
    frozenset(("poison", "fire")): "toxic_ignition",
}
LABELS = {"thermal_shock": "THERMAL SHOCK", "superconduct": "SUPERCONDUCT",
          "corruption": "CORRUPTION", "hemorrhage": "HEMORRHAGE", "overload": "OVERLOAD",
          "entropy_frost": "ENTROPY FROST", "magnetic_rupture": "MAGNETIC RUPTURE",
          "crimson_ice": "CRIMSON ICE", "toxic_ignition": "TOXIC IGNITION"}
COLORS = {"thermal_shock": (255, 170, 120), "superconduct": (150, 220, 255),
          "corruption": (200, 90, 255), "hemorrhage": (230, 60, 70),
          "overload": (255, 240, 120), "entropy_frost": (170, 150, 255),
          "magnetic_rupture": (200, 200, 255), "crimson_ice": (255, 120, 170),
          "toxic_ignition": (160, 255, 90)}

# Partner element used when a target is "exposed" (guaranteed reaction).
EXPOSE_PARTNER = {"fire": "cold", "cold": "fire", "lightning": "cold",
                  "chaos": "fire", "physical": "chaos", "bleed": "physical",
                  "poison": "fire"}

# reaction -> (surface kind, radius, duration)
REACTION_SURFACE = {
    "thermal_shock": ("water", 90, 8.0),
    "superconduct":  ("ice", 100, 6.0),
    "corruption":    ("blight", 90, 8.0),
    "overload":      ("static", 100, 5.0),
    "toxic_ignition": ("fire", 80, 5.0),
}


class ReactionSystem:
    def __init__(self, game):
        self.game = game
        self.reset()

    def reset(self):
        self.clock = 0.0
        self._auras = {}
        self._dots = []
        self._shreds = {}
        self._delayed = []
        self._frozen = {}
        self._zones = []

    # ---------------- aura plumbing ----------------
    def _entry(self, target):
        return self._auras.setdefault(id(target),
                                      {"target": target, "types": {}, "cd": 0.0, "n": 0})

    def expose(self, target, duration):
        self._entry(target)["exposed"] = self.clock + duration

    def _elements(self, player, world_rules):
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
        return types

    def _pick(self, player, target, world_rules):
        types = self._elements(player, world_rules)
        e = self._entry(target)
        n = e["n"]
        e["n"] += 1
        if len(types) == 1:
            return [types[0]]
        weapon = player.equipped.get("weapon")
        sub = getattr(weapon, "weapon_subtype", None) if weapon else None
        rest = types[1:]
        if sub == "sword":
            return [rest[(n // 3) % len(rest)] if n % 3 == 2 else types[0]]
        if sub == "greatsword":
            return list(types)
        if sub == "bow":
            moving = self.game.kinetic.player_speed > 60.0
            return [rest[n % len(rest)] if moving else types[0]]
        if sub == "long_wand":
            return [types[0]]
        return [types[n % len(types)]]            # short wand / unarmed / default

    def on_hit(self, target, hit_damage, player, world_rules):
        e = self._entry(target)

        if id(target) in self._frozen:
            self._shatter(target, hit_damage)
            if not target.alive:
                return "crimson_ice"

        types = self._pick(player, target, world_rules)

        self.game.terrain.on_element_hit(target, types, hit_damage)

        #_apply_aura = self._pick(player, target, world_rules)

        #self.game.terrain.on_element_hit(target, types, hit_damage)

        if e.get("exposed", 0) > self.clock:
            e["exposed"] = 0
            for dtype in types:
                name = REACTIONS.get(frozenset((dtype, EXPOSE_PARTNER.get(dtype))))
                if name:
                    e["types"].clear()
                    e["cd"] = self.clock + REACTION_ICD
                    self._fire(name, target, hit_damage)
                    return name

        result = None
        for dtype in types:
            r = self._apply_aura(target, dtype, hit_damage)
            result = result or r

        weapon = player.equipped.get("weapon")
        if getattr(weapon, "weapon_subtype", None) == "long_wand":
            self._drop_zone(target, hit_damage, player, world_rules)
        return result


    def _apply_aura(self, target, dtype, hit_damage):
        target = self.game.packs.redirect_aura(target)
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

    def _drop_zone(self, target, hit, player, world_rules):
        types = self._elements(player, world_rules)
        if len(types) < 2:
            return
        n = self._entry(target)["n"]
        other = types[1 + n % (len(types) - 1)]
        self._zones.append({"x": target.x, "y": target.y, "r": 80, "left": 4.0,
                            "acc": 0.0, "type": other, "hit": max(4, hit)})
        if len(self._zones) > 3:
            self._zones.pop(0)

    # ---------------- helpers ----------------
    def _within(self, x, y, radius, exclude=None):
        a = self.game.current_area
        return [o for o in list(a.enemies) + list(a.bosses)
                if o is not exclude and o.alive
                and math.hypot(o.x - x, o.y - y) <= radius]

    def _nearby(self, target, radius):
        return self._within(target.x, target.y, radius, exclude=target)

    def _ring(self, x, y, radius, color, life=0.3):
        self.game.crit_bursts.append({"x": x, "y": y, "radius": radius,
                                      "life": life, "max_life": life, "color": color})

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
                    d["stacks"] = min(8, d["stacks"] * 2)
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

    def _pull(self, o, target):
        if getattr(o, "is_boss", False) or getattr(o, "behavior", "") == "brute":
            return
        dx, dy = o.x - target.x, o.y - target.y
        d = math.hypot(dx, dy)
        if d < 40:
            return
        nx = target.x + dx / d * 36
        ny = target.y + dy / d * 36
        tm = self.game.current_area.tilemap
        if not tm.collides_rect(o._rect_at(nx, ny)):
            o.x, o.y = nx, ny

    def _shatter(self, target, hit):
        self._frozen.pop(id(target), None)
        if hasattr(target, "stagger_timer"):
            target.stagger_timer = 0.0
        base = max(4, hit)
        color = COLORS["crimson_ice"]

        self.game.terrain.add_surface(target.x, target.y, 80, "ice", 5.0, power=base)

        self.game.damage_numbers.spawn(target.x, target.y - 48, "SHATTER", color)
        self._ring(target.x, target.y, 90, color)
        for o in self._nearby(target, 90):
            self._hurt(o, base * 1.0, "bleed", color)
        self._hurt(target, base * 1.5, "bleed", color)

    # ---------------- reaction effects ----------------
    def _fire(self, name, target, hit):
        g = self.game
        color = COLORS[name]
        g.damage_numbers.spawn(target.x, target.y - 48, LABELS[name], color)
        self._ring(target.x, target.y, 70, color)
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
        elif name == "entropy_frost":
            self._hurt(target, base * 0.8, "cold", color)
            self._delayed.append({"t": 1.2, "target": target, "base": base,
                                  "amount": base * 2.2})
        elif name == "magnetic_rupture":
            for o in self._nearby(target, 160):
                self._pull(o, target)
            self._hurt(target, base * 0.8, "lightning", color)
        elif name == "crimson_ice":
            if getattr(target, "is_boss", False):
                self._hurt(target, base * 1.5, "cold", color)   # bosses can't be frozen
            elif target.alive:
                target.stagger_timer = 2.0
                self._frozen[id(target)] = {"target": target, "left": 2.0}
        elif name == "toxic_ignition":
            stacks = [d for d in self._dots if d["target"] is target
                      and d["type"] in ("chaos", "poison", "bleed")]
            pool = sum(d["dps"] * max(0.0, d["left"]) * d["stacks"] for d in stacks) * 0.6
            for d in stacks:
                self._dots.remove(d)
            total = base * 1.2 + pool
            for o in self._nearby(target, 100):
                self._hurt(o, total * 0.5, "fire", color)
            self._hurt(target, total, "fire", color)

        surf = REACTION_SURFACE.get(name)
        if surf:
            kind, radius, duration = surf
            g.terrain.add_surface(target.x, target.y, radius, kind, duration, power=base)

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

        for d in list(self._delayed):
            d["t"] -= dt
            if d["t"] > 0:
                continue
            self._delayed.remove(d)
            t = d["target"]
            if not t.alive:
                continue
            color = COLORS["entropy_frost"]
            self.game.damage_numbers.spawn(t.x, t.y - 48, "SHATTER", color)
            self._ring(t.x, t.y, 100, color)

            self.game.terrain.add_surface(t.x, t.y, 90, "ice", 5.0, power=d["base"])

            for o in self._nearby(t, 100):
                self._hurt(o, d["base"] * 0.9, "chaos", color)
            self._hurt(t, d["amount"], "cold", color)

        for k, f in list(self._frozen.items()):
            f["left"] -= dt
            if f["left"] <= 0 or not f["target"].alive:
                del self._frozen[k]

        for z in list(self._zones):
            z["left"] -= dt
            z["acc"] += dt
            if z["left"] <= 0:
                self._zones.remove(z)
                continue
            if z["acc"] >= 0.5:
                z["acc"] -= 0.5
                for o in self._within(z["x"], z["y"], z["r"]):
                    self._apply_aura(o, z["type"], z["hit"])

        for k, e in list(self._auras.items()):
            live = (any(v > self.clock for v in e["types"].values())
                    or e.get("exposed", 0) > self.clock)
            if not e["target"].alive or (not live and self.clock >= e["cd"]):
                del self._auras[k]

    def draw(self, surface, camera):
        g = self.game
        for z in self._zones:
            c = g._damage_type_color(z["type"])
            p = camera.world_to_screen((z["x"], z["y"]))
            pygame.draw.circle(surface, c, p, z["r"], 1)
            pygame.draw.circle(surface, c, p, int(z["r"] * (0.4 + 0.2 * (z["left"] % 1.0))), 1)

        for f in self._frozen.values():
            t = f["target"]
            if t.alive:
                r = camera.world_to_screen_rect(t.rect).inflate(8, 8)
                pygame.draw.rect(surface, (190, 235, 255), r, 2, border_radius=5)

        for e in self._auras.values():
            t = e["target"]
            if not t.alive:
                continue
            if e.get("exposed", 0) > self.clock:
                p = camera.world_to_screen((t.x, t.y))
                pygame.draw.circle(surface, (255, 255, 255), p,
                                   int(max(t.width, t.height) * 0.9), 1)
            live = [k for k, v in e["types"].items() if v > self.clock]
            for i, k in enumerate(live):
                off = (i - (len(live) - 1) / 2) * 10
                p = camera.world_to_screen((t.x + off, t.y - t.height / 2 - 14))
                pygame.draw.circle(surface, g._damage_type_color(k), p, 4)
                pygame.draw.circle(surface, (0, 0, 0), p, 4, 1)