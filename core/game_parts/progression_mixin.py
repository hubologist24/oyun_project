"""Events wiring, level-ups, perks/keystones, codex, dialogue, profile, shrines."""
import core.config as config
from core import audio
from core.difficulty import D
from enemies.dialogue import boss_engage_line
from history.aggregator import build_player_profile
from player.perks import roll_minor_offer, roll_keystone_offer


class ProgressionMixin:

    def _register_events(self):
        self.event_bus.subscribe("player_death", self._on_player_death)
        self.event_bus.subscribe("level_up", lambda level: self._show_message(f"Level Up! Now level {level}"))
        self.event_bus.subscribe("player_hit", self._on_player_hit_feedback)
        self.event_bus.subscribe("level_up", self._on_level_up)
        self.event_bus.subscribe("critical_hit", self._on_critical_hit)
        self.event_bus.subscribe("attacker_info", self.nemesis.on_attacker_info)
        self.event_bus.subscribe("area_entered", self.nemesis.on_area_entered)
        self.event_bus.subscribe("kinetic_technique", self._on_kinetic_technique)

    def _on_kinetic_technique(self, name=None, **kw):
        if not name:
            return
        before = self.codex.mastery_tier(name)
        newly = self.codex.record("kinetic_technique", context={"name": name})
        after = self.codex.mastery_tier(name)
        if after > before:
            from combat.kinetic import TECHNIQUES
            self._show_message(f"MASTERY {'I' * after}: {TECHNIQUES[name][0]} grows stronger!",
                               duration=3.5)
        if newly:
            self._handle_codex_unlocks(newly)

    def _on_level_up(self, level):
        audio.play("level_up")
        self.crit_bursts.append({"x": self.player.x, "y": self.player.y, "radius": 120,
                                 "life": 0.6, "max_life": 0.6, "color": (120, 220, 255)})
        self.camera.shake(magnitude=3, duration=0.15)

        D.set_player_level(level)

        if level % 5 == 0:
            self.player.extension_stones += 1
            self._show_message(f"Level {level}! Milestone: +1 Extension Stone  "
                               f"(difficulty {D.label()})", duration=4.0)

        if level % 10 == 0:
            self._milestone_bonus(level)

        self._queue_level_choice(level)

    def _milestone_bonus(self, level):
        from items.item_generator import generate_random_item
        rng = self.world.rng_service.get_stream(f"milestone_{level}")
        self.player.extension_stones += 1
        item = generate_random_item(rng, area_level=max(1, level), force_rarity="rare")
        self.current_area.loot_drops.append({
            "x": self.player.x + 30, "y": self.player.y, "item": item})
        self.crit_bursts.append({"x": self.player.x, "y": self.player.y,
                                 "radius": 180, "life": 0.9, "max_life": 0.9,
                                 "color": (255, 220, 120)})
        self.camera.shake(magnitude=6, duration=0.35)
        audio.play("evolution")
        self._show_message(f"★ LEVEL {level} MILESTONE ★  +1 bonus stone, a rare drop, "
                           f"and a KEYSTONE choice!", duration=6.0)

    # ---------------- dialogue ----------------
    def _say(self, speaker, text, duration=4.5):
        self.dialogue = {"speaker": speaker, "text": text, "t": duration}

    def _boss_greeting(self, boss):
        attempts = sum(1 for e in self.event_log.events_of_type("boss_attempt_started")
                       if e.payload.get("boss_id") == boss.name)
        fallen = [e.payload.get("name") for e in self.event_log.events_of_type("boss_killed")
                  if e.payload.get("name") != boss.name]
        self._say(boss.name, boss_engage_line(attempts, self._current_profile().main_damage, fallen))

    # ---------------- perks / keystones ----------------
    def _queue_level_choice(self, level):
        rng = self.world.rng_service.get_stream("perk_offer")
        if level % 10 == 0:
            opts = roll_keystone_offer(self.player.active_keystones(), rng)
            if opts:
                self.choice_menu.enqueue(f"KEYSTONE  -  Level {level}", opts, self._pick_keystone,
                                         subtitle="Rule-changing power. Choose one.",
                                         accent=(255, 210, 110))
                return
        opts = roll_minor_offer(rng)
        self.choice_menu.enqueue(f"LEVEL {level}  -  Choose a perk", opts, self._pick_perk,
                                 subtitle="Perks stack if you take them again.",
                                 accent=(120, 220, 255))

    def _pick_perk(self, opt):
        self.player.perks[opt["id"]] = self.player.perks.get(opt["id"], 0) + 1
        self.player.recalc_stats()
        self._show_message(f"Perk: {opt['name']}  ({opt['desc']})", duration=3.0)

    def _pick_keystone(self, opt):
        if opt["id"] not in self.player.keystones:
            self.player.keystones.append(opt["id"])
        self.player.recalc_stats()
        self.crit_bursts.append({"x": self.player.x, "y": self.player.y, "radius": 150,
                                 "life": 0.7, "max_life": 0.7, "color": (255, 210, 110)})
        audio.play("evolution")
        self._show_message(f"KEYSTONE: {opt['name']}", duration=4.0)

    # ---------------- codex ----------------
    def _handle_codex_unlocks(self, newly):
        for aid in newly:
            ach = self.codex.achievement_info(aid)
            self._show_message(
                f"CODEX UNLOCKED: {ach.get('name', aid)}!",
                duration=4.0)
        if newly and self.player is not None:
            self.player.recalc_stats()

    # ---------------- profile / shrines ----------------
    def _current_profile(self):
        p = build_player_profile(self.player, self.event_log, self.world, self.exploration)
        p.feedback = dict(self.feedback)
        return p

    def _show_profile_screen(self):
        self.profile_screen.show(self._current_profile(), codex=self.codex,
                                 player=self.player)

    def _try_interact_shrine(self) -> bool:
        for shrine in self.current_area.shrines:
            if shrine.area_id != self.current_area_id:
                continue
            if not shrine.player_in_range(self.player):
                continue
            msg = shrine.interact(self)
            if msg:
                self._show_message(msg, duration=3.5)
            return True
        return False

    def _apply_shrine_persistence(self):
        for shrine in self.current_area.shrines:
            if shrine.unique_id() in self.world.used_shrines:
                shrine.used = True
