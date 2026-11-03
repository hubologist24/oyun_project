"""World extensions, gates, gateway portal, overcharge, push-back feedback."""
import core.config as config
from world.expansion import attach_extension, find_door_site, stamp_door
from world.extension_generator import WorldExtensionGenerator
from ui.world_evolution import WorldEvolutionBanner


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

    def _ensure_starting_gate_registered(self):
        existing = self.world.gates_in_area("starting_area")
        if existing:
            for g in existing:
                stamp_door(self.starting_area, g)
            return
        #site = find_door_site(self.starting_area.tilemap.grid,
        #                      [self.starting_area.gate_room])
        site = find_door_site(self.starting_area, self.starting_area.gate_room)
        if site:
            c, r, side, _ = site
            gate = self.world.register_starting_gate(0, c, r, side)
            stamp_door(self.starting_area, gate)

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
        """
        Two-phase: STAGE a spec and show the offer banner. The world is
        not mutated until the player accepts (_accept_pending_extension).

        mode: 'manual', 'automatic', or 'debug' (bypasses playtime gate
        but NOT validation).
        """
        if self._pending_extension_spec is not None:
            return
        debug = (mode == config.EXTENSION_MODE_DEBUG)
        if not debug and self.player.extension_stones <= 0:
            self._show_message("No Extension Stones left. Bosses and elites may drop more.")
            return
        preview_gate = self._pick_gate(debug)
        if preview_gate is None:
            self._show_message(self._locked_hint(), duration=3.0)
            return

        existing_ids = {ext.extension_id for ext in self.world.extensions}
        rng = self.world.rng_service.get_stream("extension_selection")
        profile = self._current_profile()
        spec = self.extension_generator.generate(profile, existing_ids, rng)

        self._pending_extension_spec = spec
        self._pending_gate = preview_gate
        self._pending_evolution_mode = mode
        self._pending_rerolls_left = config.REROLL_CAP
        self._refresh_evolution_banner()

    def _refresh_evolution_banner(self):
        spec = self._pending_extension_spec
        gate = self._pending_gate
        debug = (self._pending_evolution_mode == config.EXTENSION_MODE_DEBUG)

        if debug:
            max_oc = WorldEvolutionBanner.MAX_OVERCHARGE
        else:
            max_oc = min(WorldEvolutionBanner.MAX_OVERCHARGE,
                         max(0, self.player.extension_stones - 1))

        # Clamp so the banner never advertises an overcharge the player
        # cannot afford (non-debug) or exceeds the hard cap (debug).
        oc = min(self.evolution_banner.overcharge_level, max_oc)
        preview_level = spec.area_level + oc * 3
        rule_names = [m["name"] for m in spec.world_modifiers]
        if oc > 0:
            rule_names.append(f"Overcharged Reality (+{oc})")

        self.evolution_banner.show(
            extension_name=spec.extension_name,
            area_level=preview_level,
            is_anomaly=spec.is_anomaly or oc > 0,
            rule_names=rule_names,
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
            self._show_message("No rerolls remaining. Accept or push back.", duration=2.0)
            return

        self._pending_rerolls_left -= 1

        existing_ids = {ext.extension_id for ext in self.world.extensions}
        rng = self.world.rng_service.get_stream("extension_selection")
        profile = self._current_profile()
        spec = self.extension_generator.generate(profile, existing_ids, rng)

        preview_gate = self._pick_gate(self._pending_evolution_mode == config.EXTENSION_MODE_DEBUG)

        self._pending_extension_spec = spec
        self._pending_gate = preview_gate
        # mode intentionally preserved across rerolls.
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
        self.event_bus.emit("area_entered", area_id=rid)
        if rid == "starting_area":
            self._show_message("Entering the Starting Region")
        else:
            ext = self.world.get_extension(rid)
            if ext:
                self._show_message(f"Entering {ext.spec.extension_name} "
                                   f"(Area Lvl {ext.spec.area_level})", duration=3.0)

    def _gate_unlocked(self, gate):
        """A region's gate is usable only once that region's boss is dead."""
        if gate.owner_area_id == "starting_area":
            return True
        return not any(b.alive and not getattr(b, "is_nemesis", False)
                       and getattr(b, "region_id", None) == gate.owner_area_id
                       for b in self.current_area.bosses)

    def _pick_gate(self, debug=False):
        rng = self.world.rng_service.get_stream("gate_selection")
        return self.world.pick_next_gate(rng, eligible=None if debug else self._gate_unlocked)

    def _locked_hint(self):
        for g in self.world.closed_gates():
            if not self._gate_unlocked(g):
                return f"Defeat the guardian of {self._area_display_name(g.owner_area_id)} to unseal more gates."
        return "The world has no sealed door left to open."

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
