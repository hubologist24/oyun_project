"""
Pack roles: enemies spawned together form a pack, and each pack gets one
(or two, if large) shared rule that makes the group more than the sum of
its members.

Pure data + small hooks. Enemies keep running their own behaviour; the
pack only (a) overrides movement for two roles, (b) applies buffs, and
(c) reacts to member deaths.
"""
import math
import pygame

ROLE_INFO = {
    "commander":     ("COMMANDER", (255, 210, 90),
                      "A Commander rallies its pack - kill it to rout them!"),
    "screen":        ("SCREEN", (190, 190, 215),
                      "Brutes screen the ranged enemies behind them!"),
    "hunter":        ("HUNTER", (200, 100, 255),
                      "Hunters lie in wait for the pack's signal..."),
    "sacrificer":    ("SACRIFICER", (255, 90, 90),
                      "Swarmers feed the Sacrificer - kill it first!"),
    "reaction_thief": ("THIEF", (120, 230, 255),
                       "A Thief siphons elemental auras from its pack!"),
    "corpse_keeper": ("KEEPER", (150, 210, 70),
                      "A Keeper taints the fallen. Watch the ground."),
}

CLUSTER_RADIUS = 280
MIN_PACK = 3
MAX_PACK = 9
BIG_PACK = 6
RALLY_RADIUS = 320
RALLY_AGGRO = 650
ALERT_RADIUS = 300
THIEF_RANGE = 240
THIEF_COOLDOWN = 1.2
SACRIFICE_MAX = 5
UPDATE_INTERVAL = 0.2


def _d(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


class PackContext:
    def __init__(self, pid, members):
        self.pid = pid
        self.members = members
        self.rules = {}            # role -> [enemies]
        self.alerted = False
        self.announced = False
        self.stacks = 0            # sacrificer stacks
        self.thief_cd = 0.0

    def handle(self, e, dt, player, tilemap, dist):
        """Movement override. Returns True if this enemy's normal behaviour
        tick should be skipped this frame."""
        role = e.pack_role
        if role == "screen":
            if dist > 520 or dist <= 90:
                return False
            ranged = [m for m in self.members if m.alive and m.behavior == "ranged"]
            if not ranged:
                return False
            r = min(ranged, key=lambda m: _d(m, e))
            rdx, rdy = player.x - r.x, player.y - r.y
            rd = math.hypot(rdx, rdy) or 1.0
            hx, hy = r.x + rdx / rd * 70, r.y + rdy / rd * 70
            if math.hypot(hx - e.x, hy - e.y) > 24:
                e._step_toward(dt, tilemap, hx - e.x, hy - e.y, 1.3)
            return True

        if role == "hunter":
            if not self.alerted:
                return dist > 100          # keep waiting unless the player is on top of us
            if 60 < dist < 600:
                e._step_toward(dt, tilemap, player.x - e.x, player.y - e.y, 1.6)
                return True
            return False
        return False


class PackManager:
    def __init__(self, game):
        self.game = game
        self._font = pygame.font.SysFont("consolas", 11, bold=True)
        self.reset()

    def reset(self):
        self.packs = []
        self.flash = []
        self.clock = 0.0
        self._tick = 0.0
        self._next = 1
        self._msg_cd = 0.0

    # ---------------- assignment ----------------
    def assign_new(self, enemies=None):
        """Group not-yet-considered enemies into packs. Safe to call repeatedly:
        every enemy considered is flagged and never reconsidered."""
        g = self.game
        enemies = enemies if enemies is not None else g.current_area.enemies
        pool = [e for e in enemies
                if e.alive and not getattr(e, "_pack_checked", False)
                and not getattr(e, "is_add", False)
                and not getattr(e, "nemesis", None)]
        for e in pool:
            e._pack_checked = True

        while pool:
            seed = pool.pop(0)
            rid = getattr(seed, "region_id", "starting_area")
            group = [seed]
            for o in pool[:]:
                if len(group) >= MAX_PACK:
                    break
                if getattr(o, "region_id", "starting_area") == rid and _d(o, seed) <= CLUSTER_RADIUS:
                    group.append(o)
                    pool.remove(o)
            if len(group) >= MIN_PACK:
                self._make_pack(group)

    @staticmethod
    def _candidates(group):
        by = lambda *b: [m for m in group if m.behavior in b]
        strong = sorted([m for m in group if m.behavior != "swarmer"],
                        key=lambda m: (m.is_elite, m.max_hp), reverse=True)
        out = {}
        if strong:
            out["commander"] = strong[:1]
        if by("brute") and by("ranged"):
            out["screen"] = by("brute")
        if by("ambusher") and by("ranged"):
            out["hunter"] = by("ambusher")
        if len(by("swarmer")) >= 2 and strong:
            out["sacrificer"] = strong[1:2] or strong[:1]
        if by("circler"):
            out["reaction_thief"] = by("circler")[:1]
        if by("chaser"):
            out["corpse_keeper"] = by("chaser")[:1]
        return out

    def _make_pack(self, group):
        rng = self.game.world.rng_service.get_stream("packs")
        pack = PackContext(self._next, group)
        self._next += 1
        for m in group:
            m.pack = pack
            m.pack_role = None
            m._pack_base_aggro = m._aggro_range

        cands = self._candidates(group)
        roles = list(cands.keys())
        rng.shuffle(roles)
        roles.sort(key=lambda r: r == "commander")      # commander last (stable)
        if "commander" in roles and rng.random() < 0.2:
            roles.remove("commander")
            roles.insert(0, "commander")

        want = 2 if len(group) >= BIG_PACK else 1
        taken = set()
        for role in roles:
            if len(pack.rules) >= want:
                break
            holders = [h for h in cands[role] if id(h) not in taken]
            if not holders:
                continue
            pack.rules[role] = holders
            for h in holders:
                taken.add(id(h))
                h.pack_role = role
                if role == "commander":
                    h.max_hp = h.hp = int(h.max_hp * 1.3)
                    h.damage = int(h.damage * 1.1)
                elif role == "sacrificer":
                    h.max_hp = h.hp = int(h.max_hp * 1.2)

        if pack.rules:
            self.packs.append(pack)
        else:
            for m in group:
                m.pack = None

    # ---------------- hooks ----------------
    def redirect_aura(self, target):
        """Reaction Thief: absorbs an aura meant for a packmate."""
        pack = getattr(target, "pack", None)
        if pack is None or self.clock < pack.thief_cd:
            return target
        for t in pack.rules.get("reaction_thief", []):
            if t.alive and t is not target and _d(t, target) <= THIEF_RANGE:
                pack.thief_cd = self.clock + THIEF_COOLDOWN
                self.flash.append({"a": (target.x, target.y), "b": (t.x, t.y), "life": 0.3})
                self.game.damage_numbers.spawn(t.x, t.y - 48, "ABSORBED", ROLE_INFO["reaction_thief"][1])
                return t
        return target

    def on_member_killed(self, enemy):
        pack = getattr(enemy, "pack", None)
        if pack is None:
            return
        g = self.game
        if enemy in pack.members:
            pack.members.remove(enemy)
        role = enemy.pack_role

        if role == "commander":
            g._show_message("The Commander falls - the pack is routed!", duration=2.5)
            for m in pack.members:
                if hasattr(m, "apply_stagger"):
                    m.apply_stagger(1.2)
                m.pack_speed_mult = 1.0
                m.pack_damage_mult = 1.0
                if not getattr(m, "nemesis", None):
                    m._aggro_range = m._pack_base_aggro

        sacs = [s for s in pack.rules.get("sacrificer", []) if s.alive and s is not enemy]
        if sacs and enemy.behavior == "swarmer":
            pack.stacks = min(SACRIFICE_MAX, pack.stacks + 1)
            s = sacs[0]
            s.hp = min(s.max_hp, s.hp + s.max_hp * 0.12)
            g.damage_numbers.spawn(s.x, s.y - 48, f"FEEDS x{pack.stacks}", ROLE_INFO["sacrificer"][1])

        keepers = [k for k in pack.rules.get("corpse_keeper", []) if k.alive and k is not enemy]
        if keepers and role != "corpse_keeper":
            g.terrain.add_surface(enemy.x, enemy.y, 60, "rot", 6.0, power=4)

    # ---------------- per-frame ----------------
    def update(self, dt):
        self.clock += dt
        for f in self.flash:
            f["life"] -= dt
        self.flash = [f for f in self.flash if f["life"] > 0]

        self._tick -= dt
        if self._tick > 0:
            return
        self._tick = UPDATE_INTERVAL

        p = self.game.player
        for pack in list(self.packs):
            pack.members = [m for m in pack.members if m.alive]
            if not pack.members:
                self.packs.remove(pack)
                continue
            m0 = pack.members[0]
            if abs(m0.x - p.x) > 1500 or abs(m0.y - p.y) > 1500:
                continue

            near_d = min(_d(m, p) for m in pack.members)
            if p.alive and not pack.alerted:
                if any(m.pack_role != "hunter" and _d(m, p) <= ALERT_RADIUS for m in pack.members):
                    pack.alerted = True
            if pack.alerted and not pack.announced and near_d < 450:
                self._announce(pack)

            cmd = [c for c in pack.rules.get("commander", []) if c.alive]
            rally = bool(cmd) and pack.alerted
            for m in pack.members:
                sp, dm = 1.0, 1.0
                if rally and m not in cmd and _d(m, cmd[0]) <= RALLY_RADIUS:
                    sp *= 1.2
                    dm *= 1.15
                if m.pack_role == "sacrificer" and pack.stacks:
                    dm *= 1 + 0.15 * pack.stacks
                    sp *= 1 + 0.05 * pack.stacks
                m.pack_speed_mult = sp
                m.pack_damage_mult = dm
                if not getattr(m, "nemesis", None):
                    m._aggro_range = max(m._pack_base_aggro, RALLY_AGGRO) if rally else m._pack_base_aggro

    def _announce(self, pack):
        pack.announced = True
        if self.clock < self._msg_cd or not pack.rules:
            return
        role = next(iter(pack.rules))
        self._msg_cd = self.clock + 8.0
        self.game._show_message(ROLE_INFO[role][2], duration=2.5)

    # ---------------- drawing ----------------
    def draw(self, surface, camera):
        p = self.game.player
        for pack in self.packs:
            for role, holders in pack.rules.items():
                label, col, _ = ROLE_INFO[role]
                if role == "sacrificer" and pack.stacks:
                    label += f" x{pack.stacks}"
                for h in holders:
                    if not h.alive or abs(h.x - p.x) > 800 or abs(h.y - p.y) > 500:
                        continue
                    sp = camera.world_to_screen((h.x, h.y))
                    pygame.draw.circle(surface, col, sp, int(max(h.width, h.height) * 0.8), 1)
                    if role == "commander" and pack.alerted:
                        pygame.draw.circle(surface, col, sp, RALLY_RADIUS, 1)
                    t = self._font.render(label, True, col)
                    surface.blit(t, (sp[0] - t.get_width() // 2, sp[1] - h.height // 2 - 36))
        for f in self.flash:
            a = camera.world_to_screen(f["a"])
            b = camera.world_to_screen(f["b"])
            pygame.draw.line(surface, ROLE_INFO["reaction_thief"][1], a, b, 2)