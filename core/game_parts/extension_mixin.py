"""World extensions, gates, gateway portal, overcharge, push-back feedback."""
import core.config as config
from world.expansion import attach_extension,  stamp_door
from world.extension_generator import WorldExtensionGenerator
from ui.world_evolution import WorldEvolutionBanner

from world.gate_tiers import GATE_TIERS

import math


class ExtensionMixin:

    def _active_world_rules(self):
        return self.world.aggregate_rule_set()

    # ---------------- setup ----------------
    def _build_extension_generator(self):
        """
        Builds the WorldExtensionGenerator with whichever backend is
        selected in core/config.py. Defaults to pure procedural.
        """
        from ai.procedural_generator import ProceduralWorldGenerator

        mode = config.AI_BACKEND_MODE
        procedural = ProceduralWorldGenerator()

        if mode == "mock_ai":
            from ai.mock_generator import MockAIWorldGenerator
            backend = MockAIWorldGenerator(failure_rate=config.MOCK_AI_FAILURE_RATE)
            print("[Game] Using MockAIWorldGenerator (offline LLM simulation) as primary backend.")
            return WorldExtensionGenerator(backend=backend, fallback_backend=procedural)

        if mode == "llm":
            from ai.llm_generator import LLMWorldGenerator
            backend = LLMWorldGenerator(provider=config.LLM_PROVIDER)
            if backend.is_available():
                print(f"[Game] Using LLMWorldGenerator (provider='{config.LLM_PROVIDER}', "
                      f"model='{backend.model}') as primary backend.")
            else:
                print(f"[Game] LLMWorldGenerator requested (provider='{config.LLM_PROVIDER}') "
                      f"but not available (missing key or package). Will fall back to procedural.")
            return WorldExtensionGenerator(backend=backend, fallback_backend=procedural)

        print("[Game] Using ProceduralWorldGenerator only.")
        return WorldExtensionGenerator(backend=procedural, fallback_backend=procedural)

    def _starting_gate_site(self, room_name="gate_room"):
        from world.map_generator import pick_wall_gate
        area = self.starting_area
        room = getattr(area, room_name, None) or area.gate_room
        return pick_wall_gate(area.tilemap.grid, room)

    def _restamp_gates(self):
        for g in self.world.gates:
            stamp_door(self.current_area, g)

    def _ensure_starting_gate_registered(self):
        existing = self.world.gates_in_area("starting_area")

        classic  = next((g for g in existing if g.is_starting_gate), None)
        gate_50  = next((g for g in existing
                        if g.seal.get("target_boss") == "The Ancient Guardian"),
                        None)
        gate_100 = next((g for g in existing
                        if g.seal.get("target_boss") == "The Hollow Warden"),
                        None)

        if classic is None:
            legacy = next((g for g in existing
                          if not g.seal.get("target_boss")), None)
            if legacy is not None:
                legacy.is_starting_gate = True
                classic = legacy
            else:
                c, r, side = self._starting_gate_site("gate_room")
                classic = self.world.register_starting_gate(
                    0, c, r, side, level_requirement=0, is_starting=True,
                    tier="safe", tier_index=0)

        if not classic.seal:
            from world.gate_seals import build_seal, SEAL_BOSS
            rng = self.world.rng_service.get_stream("gate_seal")
            classic.seal = build_seal(SEAL_BOSS, 1, rng, is_starting=True)
        classic.level_requirement = 0
        classic.tier, classic.tier_index = "safe", 0
        stamp_door(self.starting_area, classic)

        if gate_50 is None:
            c, r, side = self._starting_gate_site("gate_room_50")
            gate_50 = self.world.register_starting_gate(
                1, c, r, side, level_requirement=50,
                target_boss="The Ancient Guardian", is_starting=False,
                tier="dangerous", tier_index=2)
        gate_50.tier, gate_50.tier_index = "dangerous", 2
        stamp_door(self.starting_area, gate_50)

        if gate_100 is None:
            c, r, side = self._starting_gate_site("gate_room_100")
            gate_100 = self.world.register_starting_gate(
                2, c, r, side, level_requirement=100,
                target_boss="The Hollow Warden", is_starting=False,
                tier="extreme", tier_index=3)
        gate_100.tier, gate_100.tier_index = "extreme", 3
        stamp_door(self.starting_area, gate_100)
    # ---------------- overcharge ----------------
    def _apply_overcharge_to_spec(self, spec, overcharge):
        rng = self.world.rng_service.get_stream("overcharge")
        spec.area_level += overcharge * 3
        spec.is_anomaly = True

        chaos_dtype = rng.choice(["chaos", "lightning"])
        if not spec.loot_rules:
            spec.loot_rules = {}
        spec.loot_rules["rare_chance_multiplier"] = (
            spec.loot_rules.get("rare_chance_multiplier", 1.0) * (1 + 0.3 * overcharge))
        spec.loot_rules["six_mod_chance_multiplier"] = (
            spec.loot_rules.get("six_mod_chance_multiplier", 1.0) * (1 + 0.2 * overcharge))

        overcharge_mod = {
            "name": f"Overcharged Reality ({chaos_dtype.title()})",
            "description": f"The overcharged gate spills unstable {chaos_dtype} "
                           f"energy into this region.",
            "rules": [
                {"type": "resistance_modifier", "target": "player",
                 "damage_type": chaos_dtype, "delta": -0.20},
                {"type": "damage_dealt_multiplier", "target": "elite",
                 "damage_type": None, "multiplier": 1.25},
            ],
        }
        self.world.modifier_pool.modifiers.append(
            dict(overcharge_mod, polarity="curse"))
        spec.boss_personalization = dict(spec.boss_personalization or {})
        spec.boss_personalization["difficulty_bias"] = "high"

        # Respect MAX_MODIFIERS = 3: replace the last modifier rather than
        # adding a fourth.
        if len(spec.world_modifiers) < 3:
            spec.world_modifiers.append(overcharge_mod)
        else:
            spec.world_modifiers[-1] = overcharge_mod
        spec.boss_personalization = dict(spec.boss_personalization or {})
        spec.boss_personalization["difficulty_bias"] = "high"

    # ---------------- push-back feedback ----------------
    def _ask_pushback_reason(self, spec):
        opts = [
            {"id": "too_hard", "name": "Too hard", "desc": "Offer something gentler next time."},
            {"id": "too_easy", "name": "Too easy", "desc": "Escalate. Bring real danger."},
            {"id": "biome", "name": "Wrong place",
             "desc": f"Don't send me back to {spec.biome.replace('_', ' ').title()}."},
        ]
        self.choice_menu.enqueue("Why do you push back?", opts,
                                 lambda o: self._record_pushback(o["id"], spec),
                                 subtitle="The world is listening. (ESC: no reason)",
                                 accent=(255, 140, 140), cancellable=True)

    def _record_pushback(self, reason, spec):
        fb = self.feedback
        if reason in ("too_hard", "too_easy"):
            fb[reason] = fb.get(reason, 0) + 1
        elif reason == "biome":
            fb.setdefault("avoid_biomes", []).append(spec.biome)
            fb["avoid_biomes"] = fb["avoid_biomes"][-3:]
        self._show_message("The world takes note.", duration=2.5)

    # ---------------- extension offer flow ----------------
    def _generate_next_extension(self, mode=config.EXTENSION_MODE_MANUAL):
        if self._pending_extension_spec is not None:
            return

        eligible = self._eligible_gates()
        if not eligible:
            self._show_message(self._locked_hint(), duration=3.0)
            return

        debug = (mode == config.EXTENSION_MODE_DEBUG)
        if not debug and self.player.extension_stones <= 0:
            self._show_message(
                "No Extension Stones left. Bosses and elites may drop more.")
            return

        if len(eligible) == 1:
            self._begin_extension_for_gate(eligible[0], mode)
        else:
            self._show_gate_choice_menu(eligible, mode)

    def _eligible_gates(self):
        return [
            g for g in self.world.gates_in_area(self.current_area_id)
            if g.state == "closed" and self._gate_unlocked(g)
        ]

    def _show_gate_choice_menu(self, gates, mode):
        entries = []
        for gate in gates:
            tier_id = getattr(gate, "tier", "standard") or "standard"
            tier = GATE_TIERS.get(tier_id, GATE_TIERS["standard"])
            seal = gate.seal or {}
            preview_level = max(
                1, self.player.progression.level + tier["level_offset"])
            entries.append({
                "label": f"{tier['dest_prefix']}  (Area Lvl ~{preview_level})",
                "tier": tier_id,
                "description": tier["description"],
                "seal_desc": (
                    f"Seal: {seal.get('desc', '?')}  "
                    f"[{self._seal_progress_text(seal)}]"
                ),
                "gate": gate,
                "gate_id": gate.gate_id,
            })

        self._pending_evolution_mode = mode
        self.gateway_menu.show(
            entries,
            mode="generate",
            title="CHOOSE YOUR DESTINATION",
            subtitle="Each gate leads somewhere with different character.",
            cancellable=True,
        )

    def _handle_gate_choice(self, gate):
        if gate is None:
            return
        self.gateway_menu.hide()
        mode = self._pending_evolution_mode or config.EXTENSION_MODE_MANUAL
        self._begin_extension_for_gate(gate, mode)

    def _begin_extension_for_gate(self, gate, mode):
        existing_ids = {ext.extension_id for ext in self.world.extensions}
        rng = self.world.rng_service.get_stream("extension_selection")
        profile = self._current_profile()
        spec = self.extension_generator.generate(profile, existing_ids, rng)

        self._apply_gate_tier_to_spec(spec, gate, rng)

        self._pending_extension_spec = spec
        self._pending_gate = gate
        self._pending_evolution_mode = mode
        self._pending_rerolls_left = config.REROLL_CAP
        self._refresh_evolution_banner()

    def _apply_gate_tier_to_spec(self, spec, gate, rng):
        tier_id = getattr(gate, "tier", "standard") or "standard"
        tier = GATE_TIERS.get(tier_id, GATE_TIERS["standard"])
        player_level = self.player.progression.level

        spec.area_level = max(1, player_level + tier["level_offset"])

        if tier_id == "extreme":
            spec.is_anomaly = True

        if not spec.loot_rules:
            spec.loot_rules = {}
        else:
            spec.loot_rules = dict(spec.loot_rules)
        spec.loot_rules["rare_chance_multiplier"] = (
            spec.loot_rules.get("rare_chance_multiplier", 1.0) * tier["loot_mult"])
        spec.loot_rules["six_mod_chance_multiplier"] = (
            spec.loot_rules.get("six_mod_chance_multiplier", 1.0)
            * tier["loot_mult"])

        if not spec.boss_personalization:
            spec.boss_personalization = {}
        else:
            spec.boss_personalization = dict(spec.boss_personalization)
        spec.boss_personalization["difficulty_bias"] = tier["boss_bias"]

        if tier_id == "safe" and len(spec.world_modifiers) > 1:
            spec.world_modifiers = spec.world_modifiers[:1]

    def _refresh_evolution_banner(self):
        spec = self._pending_extension_spec
        gate = self._pending_gate
        debug = (self._pending_evolution_mode == config.EXTENSION_MODE_DEBUG)

        if debug:
            max_oc = WorldEvolutionBanner.MAX_OVERCHARGE
        else:
            max_oc = min(WorldEvolutionBanner.MAX_OVERCHARGE,
                         max(0, self.player.extension_stones - 1))

        oc = min(self.evolution_banner.overcharge_level, max_oc)
        preview_level = spec.area_level + oc * 3

        # Show the CURRENT pool, not spec.world_modifiers (which is empty here).
        pool_names = [m.get("name", "?") for m in self.world.modifier_pool.modifiers]
        if oc > 0:
            pool_names.append(f"Overcharged Reality (+{oc})")

        self.evolution_banner.show(
            extension_name=spec.extension_name,
            area_level=preview_level,
            is_anomaly=spec.is_anomaly or oc > 0,
            rule_names=pool_names,
            gate_area_name=self._area_display_name(gate.owner_area_id) if gate else None,
            reason=self._evolution_reason(self._current_profile()),
            rerolls_left=self._pending_rerolls_left,
            max_overcharge=max_oc,
        )

    def _accept_pending_extension(self):
        spec = self._pending_extension_spec
        gate = self._pending_gate
        if spec is None:
            self.evolution_banner.dismiss()
            return

        if gate is None:
            self._pending_extension_spec = None
            self._pending_gate = None
            self._pending_evolution_mode = None
            self.evolution_banner.dismiss()
            self._show_message("The world has no sealed door left to open.", duration=3.0)
            return

        overcharge = self.evolution_banner.overcharge_level
        stones_to_spend = 1 + overcharge
        debug = (self._pending_evolution_mode == config.EXTENSION_MODE_DEBUG)

        if not debug:
            if self.player.extension_stones < stones_to_spend:
                overcharge = max(0, self.player.extension_stones - 1)
                stones_to_spend = 1 + overcharge

        if overcharge > 0:
            self._apply_overcharge_to_spec(spec, overcharge)
            spec.overcharge_level = overcharge

            from ai.validators import WorldValidator, ProgressionValidator
            existing_ids = {ext.extension_id for ext in self.world.extensions}
            ok, errs = WorldValidator.validate(spec, existing_ids,
                                               self.player.progression.level)
            p_ok, p_errs = ProgressionValidator.validate(spec, self.player.progression.level)
            if not ok or not p_ok:
                print(f"[Game] Overcharged spec rejected: {errs} {p_errs}")
                self._show_message("The overcharged gate collapses -- too unstable.",
                                   duration=3.0)
                self.evolution_banner.dismiss()
                self._pending_extension_spec = None
                self._pending_gate = None
                self._pending_evolution_mode = None
                self.evolution_banner.overcharge_level = 0
                return

        extension = self.world.add_extension(spec)

        if not debug:
            self.player.extension_stones -= stones_to_spend

        opened_gate = self.world.open_gate_to(gate, spec.extension_id)
        if opened_gate is not None:
            opened_gate.overcharge_level = overcharge

        for k in ("too_hard", "too_easy"):
            self.feedback[k] = max(0, self.feedback.get(k, 0) - 1)   # feedback is "consumed"

        attach_extension(self.current_area, extension, gate,
                         self.world.rng_service, self.world)

        self.exploration.register_region_rooms(
            spec.extension_id,
            self.current_area.region_rooms.get(spec.extension_id, []),
            self.current_area.region_secret_rooms.get(spec.extension_id, set()),
        )

        self._restamp_gates()

        if overcharge > 0:
            newly = self.codex.record("overcharge_entered", amount=1)
            self._handle_codex_unlocks(newly)

        self._apply_shrine_persistence()
        self._on_map_expanded(spec.extension_id)
        self.packs.assign_new()

        self.event_bus.emit("extension_generated", extension_id=spec.extension_id,
                            mode=self._pending_evolution_mode,
                            area_level=spec.area_level)

        self._pending_extension_spec = None
        self._pending_gate = None
        self._pending_evolution_mode = None
        self._pending_rerolls_left = 0
        self.evolution_banner.dismiss()
        self.evolution_banner.overcharge_level = 0

        if opened_gate:
            self._show_message(
                f"A gate has opened in "
                f"{self._area_display_name(opened_gate.owner_area_id)}: "
                f"{spec.extension_name}", duration=3.0)
        else:
            self._show_message(f"The world shifts: {spec.extension_name}", duration=3.0)

    def _reroll_pending_extension(self):
        if self._pending_rerolls_left <= 0:
            self._show_message(
                "No rerolls remaining. Accept or push back.", duration=2.0)
            return
        if self._pending_gate is None:
            return

        self._pending_rerolls_left -= 1

        existing_ids = {ext.extension_id for ext in self.world.extensions}
        rng = self.world.rng_service.get_stream("extension_selection")
        profile = self._current_profile()
        spec = self.extension_generator.generate(profile, existing_ids, rng)
        self._apply_gate_tier_to_spec(spec, self._pending_gate, rng)

        self._pending_extension_spec = spec
        self._refresh_evolution_banner()

    def _discard_pending_extension(self):
        rejected = self._pending_extension_spec
        was_automatic = (self._pending_evolution_mode == config.EXTENSION_MODE_AUTOMATIC)

        self._pending_extension_spec = None
        self._pending_gate = None
        self._pending_evolution_mode = None
        self._pending_rerolls_left = 0
        self.evolution_banner.dismiss()
        self.evolution_banner.overcharge_level = 0

        if was_automatic:
            # Push-back on an automatic trigger must reset the clock, or
            # the banner re-fires forever.
            self.world.playtime_at_last_extension = self.world.total_playtime_seconds

        self._show_message("You declined the world's offer. It waits.", duration=3.0)

        if rejected is not None:
            self._ask_pushback_reason(rejected)

    def _evolution_reason(self, profile) -> str:
        from ai.personalization import derive_hints
        hints = derive_hints(profile)
        if hints.boss_struggling:
            return "The world noticed your struggles against bosses."
        if hints.weak_defense:
            return "The world noticed your defenses are thin."
        if hints.frequent_death_causes:
            dtype = hints.frequent_death_causes[0]
            return f"{dtype.title()} has killed you often. The world remembers."
        if hints.under_explored:
            return "You rush through regions. The world reshapes itself."
        return f"The world has been watching your {hints.dominant_damage_type} build."

    def _area_display_name(self, area_id: str) -> str:
        if area_id == "starting_area":
            return "the Starting Region"
        ext = self.world.get_extension(area_id)
        return ext.spec.extension_name if ext else area_id

    def _check_automatic_extension(self):
        return  # automatic trigger intentionally disabled
        if self.world.automatic_generation_due():
            self._generate_next_extension(mode=config.EXTENSION_MODE_AUTOMATIC)

    # ---------------- gates / travel ----------------
    
    def _update_current_region(self):
        ts = config.TILE_SIZE
        rid = self.current_area.region_at_tile(int(self.player.x // ts),
                                               int(self.player.y // ts))
        if rid == self.current_area_id:
            return
        self.current_area_id = rid
        self.current_extension_id = None if rid == "starting_area" else rid

        self.terrain.drain_pending(self.current_area, rid)
        self._on_region_entered(rid)

        self.event_bus.emit("area_entered", area_id=rid)
        if rid == "starting_area":
            self._show_message("Entering the Starting Region")
        else:
            ext = self.world.get_extension(rid)
            if ext:
                self._show_message(f"Entering {ext.spec.extension_name} "
                                   f"(Area Lvl {ext.spec.area_level})", duration=3.0)



    def _on_region_entered(self, rid):
        if rid not in self.exploration.region_rooms:
            rooms = self.current_area.region_rooms.get(rid, [])
            if rooms:
                self.exploration.register_region_rooms(
                    rid, rooms,
                    self.current_area.region_secret_rooms.get(rid, set()),
                )

    def _mark_current_room_visited(self):
        rid = self.current_area_id
        if rid is None or rid == "starting_area":
            return
        ts = config.TILE_SIZE
        col = int(self.player.x // ts)
        row = int(self.player.y // ts)
        idx = self.exploration.room_index_at_tile(rid, col, row)
        if idx is None:
            return
        if self.exploration.mark_room_visited(rid, idx):
            self._on_room_discovered(rid, idx)

    def _on_room_discovered(self, rid, idx):
        from world.room_graph import RoomType
        types = self.current_area.region_room_types.get(rid, [])
        if idx >= len(types):
            return
        if types[idx] == RoomType.SECRET:
            self._show_message("You discovered a SECRET CHAMBER!", duration=4.0)
            self.crit_bursts.append({
                "x": self.player.x, "y": self.player.y,
                "radius": 140, "life": 0.8, "max_life": 0.8,
                "color": (200, 220, 255),
            })
            self.event_bus.emit("secret_discovered",
                                region_id=rid, room_index=idx)
    
                
    def _gate_unlocked(self, gate):
        if gate is None:
            return False
        if gate.level_requirement > self.player.progression.level:
            return False
        seal = getattr(gate, "seal", None)
        if seal:
            return bool(seal.get("ready"))
        # legacy fallback
        return not any(b.alive and not getattr(b, "is_nemesis", False)
                       and getattr(b, "region_id", None) == gate.owner_area_id
                       for b in self.current_area.bosses)

    def _pick_gate(self, debug=False):
        rng = self.world.rng_service.get_stream("gate_selection")
        return self.world.pick_next_gate(rng, eligible=None if debug else self._gate_unlocked)

    def _locked_hint(self):
        g = self.world.frontier_gate()
        if g is None:
            return "The world has no sealed door left to open."
        if g.level_requirement > self.player.progression.level:
            return (f"The ancient gate is sealed until level "
                    f"{g.level_requirement} (you are "
                    f"{self.player.progression.level}).")
        seal = getattr(g, "seal", None)
        if seal and not seal.get("ready"):
            return f"Gate sealed: {seal.get('desc','?')}  {self._seal_progress_text(seal)}"
        return "The world has no sealed door left to open."


    def _guide_target(self):
        g = self.world.frontier_gate()
        if g is None:
            return None
        if g.level_requirement > self.player.progression.level:
            ts = config.TILE_SIZE
            return ((g.tile_col + 0.5) * ts, (g.tile_row + 0.5) * ts,
                    f"Level {g.level_requirement} gate")
        seal = getattr(g, "seal", None)
        if seal and seal.get("type") == "boss" and not seal.get("ready"):
            for b in self.current_area.bosses:
                if (b.alive and not getattr(b, "is_nemesis", False)
                        and getattr(b, "region_id", None) == g.owner_area_id
                        and not getattr(b, "dormant", False)):
                    return (b.x, b.y, "Guardian")
        ts = config.TILE_SIZE
        label = seal.get("label", "Gate") if seal else "Gate"
        return ((g.tile_col + 0.5) * ts, (g.tile_row + 0.5) * ts, label)



    def _seal_progress_text(self, seal):
        from world.gate_seals import seal_progress_text
        return seal_progress_text(seal)

    def _player_near_gate(self, gate, radius):
        ts = config.TILE_SIZE
        gx = gate.tile_col * ts + ts / 2
        gy = gate.tile_row * ts + ts / 2
        return math.hypot(self.player.x - gx, self.player.y - gy) < radius

    def _tick_gate_seals(self, dt):

        # Ancient Guardian awakens at level 50
        if (self.current_area_id == "starting_area"
                and self.player.progression.level >= 50):
            ancient = getattr(self.starting_area, "ancient_guardian", None)
            if ancient is not None and ancient.dormant:
                ancient.dormant = False
                if ancient not in self.current_area.bosses:
                    self.current_area.bosses.append(ancient)
                self._show_message(
                    "The Ancient Guardian STIRS — a presence older than the world awakens!",
                    duration=5.0)


        if not self.player.alive:
            return
        rid = self.current_area_id
        for gate in self.world.gates_in_area(rid):
            seal = getattr(gate, "seal", None)
            if not seal or seal.get("ready"):
                continue
            #if gate.level_requirement > self.player.progression.level:
            #    continue
            self._update_seal_progress(gate, seal, dt)

    def _update_seal_progress(self, gate, seal, dt):
        st = seal["type"]
        if st == "exploration":
            frac = self.exploration.fraction_for(gate.owner_area_id)
            seal["progress"] = frac
            if frac >= seal["target"]:
                self._complete_seal(gate, seal)
        elif st == "bargain":
            if self._player_near_gate(gate, 220):
                need = seal["target"] - seal["progress"]
                if need > 0 and self.player.extension_stones > 0:
                    pay = min(self.player.extension_stones, need)
                    self.player.extension_stones -= pay
                    seal["progress"] += pay
                    self._show_message(
                        f"Gate accepts {pay} Extension Stone(s) "
                        f"[{seal['progress']}/{seal['target']}]", duration=2.5)
                    if seal["progress"] >= seal["target"]:
                        self._complete_seal(gate, seal)
        elif st == "trial":
            if self._player_near_gate(gate, 500):
                seal["timer"] = max(0.0, (seal["timer"] or 0.0) - dt)
                if seal["timer"] <= 0.0:
                    seal["progress"] = 0
                    seal["timer"] = seal["timer_max"]
                    self._show_message("Trial failed. Timer reset.", duration=2.0)
        elif st == "survival":
            if self._player_near_gate(gate, 420):
                seal["wave_timer"] = (seal.get("wave_timer") or 0.0) - dt
                if seal["wave_timer"] <= 0:
                    self._spawn_survival_wave(gate)
                    seal["wave_timer"] = 7.0
        elif st == "corruption":
            if self._player_near_gate(gate, 800):
                dmg = max(1, int(self.player.effective_stats.max_hp * 0.015 * dt))
                self.player.effective_stats.hp = max(
                    1, self.player.effective_stats.hp - dmg)
        elif st == "shrines":
            used = sum(1 for sid in self.world.used_shrines
                       if sid.startswith(gate.owner_area_id + ":"))
            seal["progress"] = used
            if used >= seal["target"]:
                self._complete_seal(gate, seal)

    def _spawn_survival_wave(self, gate):
        from enemies.enemy import spawn_basic_enemy
        rid = gate.owner_area_id
        ext = self.world.get_extension(rid)
        lvl = ext.spec.area_level if ext else max(2, self.player.progression.level)
        rng = self.world.rng_service.get_stream(f"seal_wave:{rid}")
        tm = self.current_area.tilemap
        ts = config.TILE_SIZE
        gx = gate.tile_col * ts + ts / 2
        gy = gate.tile_row * ts + ts / 2
        n = min(4, 2 + lvl // 4)
        for _ in range(n):
            for _try in range(15):
                ang = rng.uniform(0, math.tau)
                dist = rng.uniform(200, 320)
                x, y = gx + math.cos(ang) * dist, gy + math.sin(ang) * dist
                import pygame
                if tm.collides_rect(pygame.Rect(int(x - 14), int(y - 14), 28, 28)):
                    continue
                e = spawn_basic_enemy(x, y, rng, area_level=lvl)
                e.region_id = rid
                e.is_seal_add = True
                e._pack_checked = True
                self.current_area.enemies.append(e)
                break
        self._show_message("The seal's wardens rise...", duration=1.5)

    def _complete_seal(self, gate, seal):
        seal["ready"] = True
        self._show_message(
            f"✦ {seal['label']} SEAL BROKEN ✦  {gate.owner_area_id.replace('_',' ').title()}",
            duration=4.0)
        self.event_bus.emit("gate_unsealed", gate_id=gate.gate_id)
        self.crit_bursts.append({
            "x": (gate.tile_col + 0.5) * config.TILE_SIZE,
            "y": (gate.tile_row + 0.5) * config.TILE_SIZE,
            "radius": 140, "life": 0.8, "max_life": 0.8,
            "color": (255, 220, 120),
        })

    def _advance_seal_on_kill(self, enemy):
        rid = self.current_area_id
        for gate in self.world.gates_in_area(rid):
            seal = getattr(gate, "seal", None)
            if not seal or seal.get("ready"):
                continue
            st = seal["type"]
            advance = False
            if st == "purge":
                advance = True
            elif st == "elite_hunt" and enemy.is_elite:
                advance = True
            elif st == "destroy_spawners" and getattr(enemy, "is_spawner", False):
                advance = True
            elif st == "hunt_convoy" and getattr(enemy, "is_convoy_carrier", False):
                advance = True
            elif st == "survival" and self._player_near_gate(gate, 420):
                advance = True
            elif st == "trial" and self._player_near_gate(gate, 500):
                advance = True
            elif st == "corruption" and self._player_near_gate(gate, 800):
                advance = True
            if advance:
                seal["progress"] += 1
                if seal["progress"] >= seal["target"]:
                    self._complete_seal(gate, seal)

    def _advance_seal_on_boss(self, boss):
        rid = getattr(boss, "region_id", self.current_area_id)
        for gate in self.world.gates_in_area(rid):
            seal = getattr(gate, "seal", None)
            if not seal or seal.get("ready"):
                continue
            st = seal["type"]
            if st == "boss":
                target = seal.get("target_boss")
                if target and boss.name != target:
                    continue                                  # ← per-gate filtering
                seal["progress"] = 1
                self._complete_seal(gate, seal)
            elif st == "hunt_nemesis":
                # If a true nemesis exists it takes priority, but any region
                # guardian also satisfies the seal so it isn't a dead-end.
                if (getattr(boss, "nemesis", None)
                        or getattr(boss, "region_id", None) == gate.owner_area_id):
                    seal["progress"] = 1
                    self._complete_seal(gate, seal)

    def _teleport_to_region(self, rid):
        pos = self.current_area.region_start.get(rid)
        if not pos:
            return
        self._clear_player_projectiles()
        self.player.x, self.player.y = pos
        self.player.clear_move_target()
        self._update_current_region()
        self.camera.update(self.player.x, self.player.y)

    def _on_map_expanded(self, rid):
        tm = self.current_area.tilemap
        self.camera.world_w, self.camera.world_h = tm.pixel_size()
        self.exploration.register_region(rid, self.current_area.region_walkable_count(rid))

    def _handle_gateway_menu_select(self, target_area_id):
        self.gateway_menu.hide()
        self._teleport_to_region(target_area_id)

    def _open_gateway_menu(self):
        near = self.nemesis.regions_with_nemesis()

        def warn(rid):
            return f"   !! {' & '.join(near[rid]).upper()} IS NEAR !!" if rid in near else ""

        entries = []
        if self.current_area_id != "starting_area":
            entries.append({"label": "Starting Region" + warn("starting_area"),
                            "target_area_id": "starting_area"})
        for ext in self.world.extensions:
            if ext.extension_id != self.current_area_id:
                entries.append({
                    "label": f"{ext.spec.extension_name}   (Lvl {ext.spec.area_level})"
                             + warn(ext.extension_id),
                    "target_area_id": ext.extension_id})
        if not entries:
            self._show_message("Nothing to travel to yet. Trigger world evolution first.")
            return
        self.gateway_menu.show(entries)
