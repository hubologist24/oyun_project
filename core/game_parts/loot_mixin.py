"""Kills, loot, boss death, roaming spawns, boss adds, starting gear."""
import math
import pygame

import core.config as config
from core.difficulty import D
from enemies.enemy import spawn_basic_enemy
from enemies.dialogue import boss_last_words
from core.game_parts.constants import MAX_ON_KILL_DEPTH


class LootMixin:

    # ---------------- roaming / adds ----------------
    def _region_has_living_boss(self, rid):
        """A region is 'pacified' once its guardian is dead."""
        return any(b.alive and getattr(b, "region_id", None) == rid
                   for b in self.current_area.bosses)

    def _update_roaming_spawns(self, dt):
        self._roam_timer -= dt
        if self._roam_timer > 0:
            return
        self._roam_timer = D.get("roam_interval")
        px, py = self.player.x, self.player.y
        near = sum(1 for e in self.current_area.enemies
                   if abs(e.x - px) < 1000 and abs(e.y - py) < 1000)
        if near >= D.get_int("roam_cap"):
            return
        ts = config.TILE_SIZE
        grid = self.current_area.tilemap.grid
        rng = self.world.rng_service.get_stream("roaming")
        pc, pr = int(px // ts), int(py // ts)
        for _ in range(60):
            c, r = pc + rng.randint(-24, 24), pr + rng.randint(-16, 16)
            if not (0 <= r < len(grid) and 0 <= c < len(grid[0])) or grid[r][c] != 0:
                continue
            x, y = c * ts + ts / 2, r * ts + ts / 2
            if math.hypot(x - px, y - py) < 520:
                continue
            rid = self.current_area.region_at_tile(c, r)

            # Don't feed roamers into a region whose guardian is dead.
            if not self._region_has_living_boss(rid):
                continue

            ext = self.world.get_extension(rid)
            lvl = ext.spec.area_level if ext else max(2, self.player.progression.level)
            e = spawn_basic_enemy(x, y, rng, area_level=lvl)
            e.region_id = rid
            e._pack_checked = True
            self.current_area.enemies.append(e)
            break

    def _spawn_boss_adds(self, boss):
        n, boss.summon_requests = boss.summon_requests, 0
        alive_adds = sum(1 for e in self.current_area.enemies
                         if getattr(e, "is_add", False) and e.alive)
        n = min(n, 14 - alive_adds)
        if n <= 0:
            return
        rid = getattr(boss, "region_id", "starting_area")
        ext = self.world.get_extension(rid)
        lvl = ext.spec.area_level if ext else 3
        rng = self.world.rng_service.get_stream("boss_adds")
        tm = self.current_area.tilemap
        for _ in range(n):
            for _try in range(10):
                x = boss.x + rng.randint(-140, 140)
                y = boss.y + rng.randint(-140, 140)
                if tm.collides_rect(pygame.Rect(int(x - 10), int(y - 10), 20, 20)):
                    continue
                e = spawn_basic_enemy(x, y, rng, area_level=lvl, force_behavior="swarmer")
                e.region_id = rid
                e.is_add = True
                e.xp_reward = 3
                self.current_area.enemies.append(e)
                break
        self._show_message("The guardian summons minions!", duration=1.5)

    # ---------------- kills ----------------
    def _on_enemy_killed(self, enemy, _depth=0):
        if enemy not in self.current_area.enemies:
            return
        self.current_area.enemies.remove(enemy)

        self.packs.on_member_killed(enemy)
        self.terrain.on_enemy_killed(enemy)

        diff = self.player.progression.level - enemy.area_level
        if diff <= 2:
            xp_mult = min(1.3, 1.0 + 0.05 * max(0, -diff))   # slight bonus for punching up
        else:
            xp_mult = max(0.1, 1.0 - 0.15 * (diff - 2))      # grey-out farming
        gained = max(1, int(enemy.xp_reward * xp_mult))
        self.player.gain_xp(gained)
        self.damage_numbers.spawn(enemy.x, enemy.y - 45, f"+{gained} XP", (110, 180, 255))

        self.event_bus.emit("enemy_killed", name=enemy.name)

        newly = self.codex.record("enemy_killed")
        if getattr(enemy, "pack_role", None) == "commander":
            newly += self.codex.record("commander_killed")
        self._handle_codex_unlocks(newly)

        # --- kill juice ---
        self.crit_bursts.append({
            "x": enemy.x, "y": enemy.y, "radius": 32,
            "life": 0.18, "max_life": 0.18,
            "color": (255, 235, 190),
        })
        if enemy.is_elite:
            self.camera.shake(magnitude=4, duration=0.12)

        rules = self._active_world_rules()

        if "explosive" in getattr(enemy, "affixes", ()):
            self.damage_numbers.spawn(enemy.x, enemy.y - 30, "BOOM", (255, 140, 40))
            if self.player.alive and math.hypot(enemy.x - self.player.x,
                                                enemy.y - self.player.y) < 90:
                dmg = max(1, int(enemy.damage * 2))
                if self.player.take_damage(dmg, cause="fire"):
                    self.event_bus.emit("player_hit", source=enemy.name,
                                        damage=dmg, damage_type="fire")

        if getattr(enemy, "nemesis", None):
            self.nemesis.on_killed(enemy)
            return

        if getattr(enemy, "is_add", False):      # boss minions drop nothing
            return

        # final = LOOT_CONFIG_base * elite_mult * D.get(...) * global_mult
        rng = self.world.rng_service.get_stream("loot")
        cfg = config.LOOT_CONFIG
        g = cfg.get("global_mult", 1.0)
        elite = bool(enemy.is_elite)

        # --- stone (world-extension currency) ---
        stone_chance = (cfg["enemy_stone_chance"]
                        * (cfg["elite_stone_mult"] if elite else 1.0)
                        * D.get("stone_drop_mult") * g)
        if rng.random() < stone_chance:
            self.current_area.spawn_pickup(enemy.x + 12, enemy.y, "stone")

        # --- item first; orb only if the item roll failed ---
        item_chance = (cfg["enemy_item_chance"]
                       * (cfg["elite_item_mult"] if elite else 1.0)
                       * D.get("loot_drop_mult") * g)
        if rng.random() < item_chance:
            self.current_area.spawn_loot(
                enemy.x, enemy.y, area_level=enemy.area_level,
                world_rules=rules,
            )
        else:
            orb_chance = (cfg["enemy_orb_chance"]
                          * (cfg["elite_orb_mult"] if elite else 1.0)
                          * D.get("orb_drop_mult") * g)
            if rng.random() < orb_chance:
                self.current_area.spawn_orb(enemy.x, enemy.y)

        self._resolve_on_kill_effects(enemy, rules, _depth)

    def _resolve_on_kill_effects(self, victim, rules, depth):
        if depth >= MAX_ON_KILL_DEPTH:
            return
        from combat.combat import _enemy_category
        from combat.damage import resolve_damage
        for eff in rules.on_kill_effects_for(_enemy_category(victim)):
            kind = eff.get("effect")
            if kind == "heal_player":
                s = self.player.effective_stats
                amt = int(s.max_hp * eff.get("percent", 0.05))
                s.hp = min(s.max_hp, s.hp + amt)
                self.damage_numbers.spawn(self.player.x, self.player.y - 30, f"+{amt}", (90, 255, 120))
            elif kind == "explode":
                radius = eff.get("radius", 80)
                dtype = eff.get("damage_type", "fire")
                base = victim.max_hp * eff.get("damage_ratio", 0.3)
                for other in list(self.current_area.enemies):
                    if other is victim or not other.alive:
                        continue
                    if math.hypot(other.x - victim.x, other.y - victim.y) > radius:
                        continue
                    dmg = resolve_damage(base, dtype, None,
                                         target_resistances=other.resistances,
                                         world_rules=rules,
                                         target_category=_enemy_category(other),
                                         source_category="all")
                    other.take_damage(dmg)
                    self.damage_numbers.spawn(other.x, other.y - 25, str(dmg), (255, 150, 60))
                    if not other.alive:
                        self._on_enemy_killed(other, depth + 1)

    def _roll_boss_loot(self, source, level, world_rules, rng,
                        world_boss: bool = False, loot_rules_override=None):
        """Shared loot roll for bosses AND nemesis kills. global_mult scales
        items + stones but NOT the always-orb."""
        cfg = config.LOOT_CONFIG
        g = cfg.get("global_mult", 1.0)

        # --- items ---
        slots = list(cfg["boss_item_slots"])
        if world_boss:
            bonus = cfg["world_boss_item_bonus"]
            slots = [(min(1.0, c + bonus), a) for c, a in slots]
            extra = cfg.get("world_boss_extra_slot")
            if extra:
                slots.append(extra)

        total_slots = len(slots)
        boss_mult = D.get("boss_loot_mult") * g
        for i, (chance, counts) in enumerate(slots):
            if rng.random() >= min(1.0, chance * boss_mult):
                continue
            x_offset = (i - (total_slots - 1) / 2) * 30
            kwargs = {"area_level": level, "world_rules": world_rules,
                      "force_affix_counts": counts}
            if loot_rules_override is not None:
                kwargs["loot_rules_override"] = loot_rules_override
            self.current_area.spawn_loot(source.x + x_offset, source.y, **kwargs)

        # --- stones ---
        stones_dropped = 0
        stone_mult = D.get("boss_stone_mult") * g
        for base_chance in cfg["boss_stone_chances"]:
            chance = (base_chance
                      + (cfg["world_boss_stone_bonus"] if world_boss else 0.0)) \
                     * stone_mult
            if rng.random() < chance:
                self.current_area.spawn_pickup(
                    source.x + 40 + stones_dropped * 18, source.y + 30, "stone")
                stones_dropped += 1

        # --- health orb (always; NOT scaled by global_mult) ---
        if cfg.get("boss_orb_always", True):
            self.current_area.spawn_orb(source.x, source.y + 30)

    def _on_boss_killed(self, boss):
        if getattr(boss, "_death_handled", False):
            return
        boss._death_handled = True
        if getattr(boss, "nemesis", None):
            self.nemesis.on_killed(boss)
            return
        self._say(boss.name, boss_last_words(), 4.0)

        rid = getattr(boss, "region_id", "starting_area")
        pressure_level = self._pressure_level(rid)
        newly = self.codex.record("boss_killed")

        # Flawless = player has never died during an attempt against THIS boss.
        prior_deaths = sum(
            1 for e in self.event_log.events_of_type("boss_attempt_ended")
            if e.payload.get("boss_id") == boss.name
            and e.payload.get("result") == "death"
        )
        if prior_deaths == 0 and not getattr(boss, "is_nemesis", False):
            newly += self.codex.record("flawless_boss")

        if (not getattr(boss, "is_nemesis", False)
                and getattr(config, "DIFFICULTY_ADAPTIVE", False)
                and prior_deaths == 0):
            D.adjust(getattr(config, "DIFFICULTY_ADAPTIVE_UP", 2))
            self._show_message(f"Difficulty adjusts: {D.label()}", duration=2.0)
        self._deaths_since_boss_kill = 0

        if pressure_level >= 4:
            newly += self.codex.record("pressure_kill")

        # Survivor of a full run through every phase.
        if boss.phase_index >= max(0, len(boss.template.phases) - 1):
            newly += self.codex.record("survived_full_boss")

        self._handle_codex_unlocks(newly)
        # Reset region pressure - guardian is dead
        self.world.region_presence[rid] = 0.0

        self._show_message(
            f"{boss.name.upper()} HAS FALLEN.  {self._area_display_name(rid)} is pacified.",
            duration=6.0)
        self.event_bus.emit("boss_killed", name=boss.name)
        if self._boss_attempt_active:
            self.event_bus.emit("boss_attempt_ended", result="victory",
                                boss_id=self._boss_attempt_boss_id)
            self._boss_attempt_active = False
            self._boss_attempt_boss_id = None

        # kill its summoned minions
        for e in list(self.current_area.enemies):
            if getattr(e, "is_add", False):
                e.take_damage(10**9)
                self.current_area.enemies.remove(e)

        rules = self._active_world_rules()
        ext = self.world.get_extension(rid)
        lvl = max(self.player.progression.level, ext.spec.area_level if ext else 3) + 2
        rng = self.world.rng_service.get_stream("boss_loot")
        self._roll_boss_loot(boss, lvl, rules, rng, world_boss=False)
        self.world.cleared_regions.add(rid)

    def _pickup_loot(self):
        px, py = self.player.x, self.player.y
        for drop in list(self.current_area.loot_drops):
            dist = ((drop["x"] - px) ** 2 + (drop["y"] - py) ** 2) ** 0.5
            if dist >= 30:
                continue
            if drop.get("kind") == "heal":
                s = self.player.effective_stats
                if s.hp < s.max_hp:
                    amt = max(1, int(s.max_hp * D.get("orb_heal_frac")))
                    s.hp = min(s.max_hp, s.hp + amt)
                    self.current_area.loot_drops.remove(drop)
                    self.damage_numbers.spawn(px, py - 30, f"+{amt}", (90, 255, 120))
                continue
            if drop.get("kind") == "stone":
                self.player.extension_stones += 1
                self.current_area.loot_drops.remove(drop)
                self._show_message(f"+1 Extension Stone ({self.player.extension_stones})", duration=2.0)
                continue
            if self.player.inventory.add_item(drop["item"]):
                self.current_area.loot_drops.remove(drop)
                item = drop["item"]
                self.event_bus.emit("item_found", item_id=item.item_id, rarity=item.rarity,
                                    is_six_mod=item.is_six_mod())
                tag = item.affix_count_label()
                if item.is_six_mod():
                    self._show_message(f"★★★ EXCEPTIONAL ITEM: {item.display_name} [{tag}] ★★★",
                                       duration=5.0)
                else:
                    self._show_message(f"Picked up {item.display_name} [{tag}]", duration=1.4)

    def _grant_starting_gear(self):
        """Grant a 1-affix sword + 1-affix armor on a fresh start.
        Idempotent: does nothing if the player already has a weapon."""
        if self.player.equipped.get("weapon") is not None:
            return

        from items.item_generator import generate_random_item
        rng = self.world.rng_service.get_stream("starting_gear")
        try:
            weapon = generate_random_item(
                rng, area_level=1, force_slot="weapon",
                force_weapon_subtype="sword", force_affix_counts=(1, 0))
            armor = generate_random_item(
                rng, area_level=1, force_slot="armor", force_affix_counts=(0, 1))
        except Exception as e:
            print(f"[Game] _grant_starting_gear generation failed: {e}")
            return

        for label, item in (("weapon", weapon), ("armor", armor)):
            if item is None:
                continue
            if self.player.equip(item):
                print(f"[Game] starter {label} equipped: "
                      f"{item.display_name} [{item.affix_count_label()}]")
            elif self.player.inventory.add_item(item):
                print(f"[Game] starter {label} granted to inventory: "
                      f"{item.display_name} (equip failed)")
            else:
                print(f"[Game] starter {label} LOST (inventory full)")

        self._show_message(
            f"Starter gear: {weapon.display_name} + {armor.display_name}",
            duration=4.0)
