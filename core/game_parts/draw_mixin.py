# [PROJECT: core]
# [SYMBOLS] classes: DrawMixin
import textwrap
import pygame

import core.config as config
from core.game_parts.constants import PRESSURE_COLORS

from ui.objective_pointer import draw_objective_pointer

from ui.gate_seal_widget import draw_gate_seal_widget


class DrawMixin:

    def _font(self, size, bold=False):
        cache = self.__dict__.setdefault("_font_cache", {})
        key = (size, bold)
        f = cache.get(key)
        if f is None:
            f = pygame.font.SysFont("consolas", size, bold=bold)
            cache[key] = f
        return f

    def _draw(self):
        self.screen.fill(config.COLOR_BG)

        gates_here = self.world.gates_in_area(self.current_area_id)
        self.current_area.draw(self.screen, self.camera, gates=gates_here)
        self._draw_region_atmosphere()

        self.terrain.draw(self.screen, self.camera)

        if self.current_area_id == "starting_area":
            safe_scr = self.camera.world_to_screen_rect(self.starting_area.safe_room_rect)
            pygame.draw.rect(self.screen, (70, 160, 90), safe_scr, 2, border_radius=10)
            label = self._info_font.render("SAFE ZONE", True, (120, 220, 150))
            self.screen.blit(label, (safe_scr.centerx - label.get_width() // 2,
                                     safe_scr.top + 6))

        for shrine in self.current_area.shrines:
            shrine.draw(self.screen, self.camera)

        for enemy in self.current_area.enemies:
            if enemy.alive:
                enemy.draw(self.screen, self.camera)

        for boss in self.current_area.bosses:
            if boss.alive:
                boss.draw(self.screen, self.camera)

        self.packs.draw(self.screen, self.camera)
        self.reactions.draw(self.screen, self.camera)

        if self.player.alive:
            self.player.draw(self.screen, self.camera)

        self.nemesis.draw(self.screen, self.camera)

        self._draw_swing_fx()

        boss_for_hud = self._nearest_boss()
        self.damage_numbers.draw(self.screen, self.camera)
        #self.hud.draw(self.screen, self.player, boss=boss_for_hud, message=self.message)

        if not (self.evolution_banner.visible or self.profile_screen.visible
                or self.gateway_menu.visible or self.inventory_ui.visible
                or self.choice_menu.visible):
            target = self._guide_target()
            if target:
                draw_objective_pointer(self.screen, self.camera,
                                        self.player.x, self.player.y,
                                        *target)

        self.hud.draw(self.screen, self.player, boss=boss_for_hud,
                      message=self.message)

        draw_gate_seal_widget(self.screen, self,
                              show_detail=getattr(self, "show_seal_detail", False),
                              multi_gate_hint=self._gate_count_hint())

        self._draw_dialogue()

        self._draw_player_projectiles()
        self._draw_crit_bursts()
        self.inventory_ui.draw(self.screen, self.player, world_rules=self._active_world_rules())
        self._draw_area_label()
        self.evolution_banner.draw(self.screen)
        self.profile_screen.draw(self.screen)
        self.gateway_menu.draw(self.screen)

        self.choice_menu.draw(self.screen)

        if not self.inventory_ui.visible:
            self._draw_region_info()

        for shrine in self.current_area.shrines:
            if (shrine.area_id == self.current_area_id
                    and shrine.usable
                    and shrine.player_in_range(self.player)):
                sx, sy = self.camera.world_to_screen((shrine.x, shrine.y))
                hint = self._info_font_b.render(shrine.interaction_hint(), True, (255, 240, 170))
                self.screen.blit(hint, (sx - hint.get_width() // 2, sy - 66))
                break

        pygame.display.flip()

    def _draw_dialogue(self):
        if not self.dialogue:
            return
        d = self.dialogue
        txt = self._info_font_b.render(f'{d["speaker"]}: "{d["text"]}"', True, (255, 210, 230))
        bg = pygame.Surface((txt.get_width() + 24, txt.get_height() + 12), pygame.SRCALPHA)
        bg.fill((10, 10, 16, 200))
        x = config.SCREEN_WIDTH // 2 - bg.get_width() // 2
        y = config.SCREEN_HEIGHT - 130
        self.screen.blit(bg, (x, y))
        self.screen.blit(txt, (x + 12, y + 6))


    def _gate_count_hint(self):
        gates = self.world.gates_in_area(self.current_area_id)
        closed = sum(1 for g in gates if g.state == "closed")
        return closed if closed > 1 else 0

    def _draw_swing_fx(self):
        from world import swing_fx
        if not self.player_swings:
            return
        self._fx_surface.fill((0, 0, 0, 0))
        for fx in self.player_swings:
            pts = swing_fx.build_polygon_points(fx, self.camera.world_to_screen)
            frac = fx["life"] / fx["max_life"]
            alpha = int(150 * frac)
            fill = (255, 235, 130, alpha) if fx["arc_deg"] >= 150 else (200, 230, 255, alpha)
            pygame.draw.polygon(self._fx_surface, fill, pts)
            pygame.draw.lines(self._fx_surface,
                              (255, 255, 255, min(255, alpha + 80)),
                              True, pts, 2)
        self.screen.blit(self._fx_surface, (0, 0))

    def _draw_crit_bursts(self):
        for b in self.crit_bursts:
            frac = b["life"] / b["max_life"]
            current_r = b["radius"] * (1.0 - frac)
            if current_r < 2:
                continue
            center = self.camera.world_to_screen((b["x"], b["y"]))
            r, g, bl = b["color"]
            faded = (int(r * frac + 40 * (1 - frac)),
                     int(g * frac + 40 * (1 - frac)),
                     int(bl * frac + 40 * (1 - frac)))
            width = max(1, int(4 * frac))
            pygame.draw.circle(self.screen, faded, center, int(current_r), width)

    def _draw_player_projectiles(self):
        for proj in self.player_projectiles:
            pos = self.camera.world_to_screen((proj.x, proj.y))
            pygame.draw.circle(self.screen, (255, 220, 120), pos, proj.radius)
            pygame.draw.circle(self.screen, (255, 255, 255), pos, proj.radius, 1)

    def _short_modifier_effect(self, mod):
        for rule in mod.get("rules", []):
            t = rule.get("type")
            if t == "damage_taken_multiplier":
                tgt = rule.get("target", "all")
                dt  = rule.get("damage_type") or "all"
                m   = rule.get("multiplier", 1.0)
                sign = "↑" if m > 1 else "↓"
                return f"{tgt} takes {sign}{int(abs(m-1)*100)}% {dt}"
            if t == "damage_dealt_multiplier":
                dt = rule.get("damage_type") or "all"
                m  = rule.get("multiplier", 1.0)
                sign = "↑" if m > 1 else "↓"
                return f"{dt} dmg {sign}{int(abs(m-1)*100)}%"
            if t == "conversion":
                return f"{rule['from_type']}→{rule['to_type']} {int(rule['percent']*100)}%"
            if t == "resistance_modifier":
                dt = rule.get("damage_type", "?")
                d  = rule.get("delta", 0.0)
                sign = "+" if d > 0 else ""
                return f"{dt} resist {sign}{int(d*100)}%"
            if t == "tier_override":
                return f"{rule.get('modifier','?')} tiers shifted"
            if t == "on_kill_effect":
                return f"on kill: {rule.get('effect','?')}"
        return "world rule"

    # ---------------------------------------------------------------- CHANGED
    def _draw_region_info(self):
        rid = self.current_area_id
        ext = self.world.get_extension(rid) if rid != "starting_area" else None
        W = 360
        lines = []

        # --- header ---
        if ext is None:
            lines.append(("Starting Region", (255, 255, 255), True))
            lines.append(("Area Lvl 1  -  the original world", (200, 200, 210), False))
        else:
            s = ext.spec
            lines.append((s.extension_name, (255, 255, 255), True))
            tag = "  [ANOMALY]" if s.is_anomaly else ""
            if s.overcharge_level > 0:
                tag += f"  [OVERCHARGE +{s.overcharge_level}]"
            lines.append((f"Area Lvl {s.area_level}{tag}",
                          (255, 120, 120) if s.is_anomaly else (200, 200, 210), False))
            gate = next((g for g in self.world.gates
                         if g.target_extension_id == rid), None)
            if gate:
                lines.append((f"Opened from: {self._area_display_name(gate.owner_area_id)}",
                              (140, 220, 255), False))

        # --- global world attunement (the modifier pool) ---
        pool = self.world.modifier_pool
        counts = pool.polarity_counts()
        lines.append((f"World Attunement [{pool.size()}/{pool.ceiling}]  "
                      f"({counts['buff']} blessings, {counts['curse']} curses)",
                      (220, 220, 240), True))

        max_shown = 999 if self.show_region_details else 4
        shown = 0
        for m in pool.modifiers:
            if shown >= max_shown:
                remaining = pool.size() - shown
                if remaining > 0:
                    lines.append((f"  ... +{remaining} more  (F4 to expand)",
                                  (150, 150, 160), False))
                break
            pol = m.get("polarity", "neutral")
            color = (150, 230, 150) if pol == "buff" else (230, 140, 140)
            tag = "+" if pol == "buff" else ("−" if pol == "curse" else "·")
            name = m.get("name", "?")
            if self.show_region_details:
                lines.append((f"  {tag} {name}", color, False))
                for w in textwrap.wrap(m.get("description", ""), 40):
                    lines.append((f"     {w}", (140, 170, 140), False))
            else:
                short = self._short_modifier_effect(m)
                lines.append((f"  {tag} {name}  —  {short}", color, False))
            shown += 1

        # --- what THIS region changed (delta in spec.world_modifiers) ---
        if ext is not None and ext.spec.world_modifiers:
            added = [m for m in ext.spec.world_modifiers if not m.get("_removed")]
            removed = [m for m in ext.spec.world_modifiers if m.get("_removed")]
            if added:
                lines.append(("This region added:", (200, 180, 120), False))
                for m in added:
                    lines.append((f"  + {m.get('name', '?')}", (220, 200, 140), False))
            if removed:
                lines.append(("This region lifted:", (160, 140, 160), False))
                for m in removed:
                    lines.append((f"  \u2212 {m.get('name', '?')}", (200, 160, 200), False))

        # --- enemies / boss ---
        alive = sum(1 for e in self.current_area.enemies
                    if e.alive and getattr(e, "region_id", "starting_area") == rid)
        lines.append((f"Enemies left: {alive}", (230, 200, 200), False))
        for b in self.current_area.bosses:
            if getattr(b, "region_id", "starting_area") == rid:
                lines.append((f"Boss: {b.name} ({'alive' if b.alive else 'defeated'})",
                              (220, 150, 240), False))

        if self.show_region_details and ext is not None:
            fav = ext.spec.loot_rules.get("favored_damage_types", {})
            if fav:
                lines.append(("Loot favors: " + ", ".join(fav.keys()), (240, 220, 120), False))
            arch = ext.spec.boss_personalization.get("mechanic_archetype")
            if arch:
                lines.append((f"Boss style: {arch}", (240, 220, 120), False))

        level = self._pressure_level(rid)
        if level > 0:
            label = self._pressure_label(level)
            color = PRESSURE_COLORS[level]
            lines.append((f"World Pressure: {label}", color, level >= 2))

        lines.append((f"Explored {self.exploration.fraction_for(rid) * 100:.0f}%   "
                      f"(F4: {'less' if self.show_region_details else 'more'})",
                      (150, 150, 160), False))

        rooms_total = self.exploration.rooms_total_for(rid)
        if rooms_total > 0:
            rooms_seen = self.exploration.rooms_discovered_for(rid)
            lines.append((f"Rooms found: {rooms_seen}/{rooms_total}",
                          (200, 220, 255), False))

        # --- panel ---
        h = 16 + sum(22 if b else 18 for _, _, b in lines)
        panel = pygame.Surface((W, h), pygame.SRCALPHA)
        panel.fill((10, 10, 16, 190))
        self.screen.blit(panel, (config.SCREEN_WIDTH - W - 12, 12))
        y = 20
        for text, color, bold in lines:
            font = self._info_font_b if bold else self._info_font
            self.screen.blit(font.render(text, True, color),
                             (config.SCREEN_WIDTH - W - 4, y))
            y += 22 if bold else 18

    def _draw_area_label(self):
        font = self._font(18, True)
        if self.current_extension_id is None:
            label = "Starting Region  (TAB: Gateway Portal | F6: Profile | F11: Generate Extension)"
        else:
            ext = self.world.get_extension(self.current_extension_id)
            label = f"{ext.spec.extension_name}  |  Area Lvl {ext.spec.area_level}  (TAB: Gateway Portal)"
        explored = self.exploration.fraction_for(self.current_area_id) * 100
        label += f"  |  Explored {explored:.0f}%"
        text = font.render(label, True, (220, 220, 230))
        self.screen.blit(text, (config.SCREEN_WIDTH // 2 - text.get_width() // 2,
                                config.SCREEN_HEIGHT - 24))
        if self.player.combo_count >= 2:
            font = self._font(22, True)
            pct = int(min(self.player.combo_count, 10) * 6)
            txt = font.render(f"COMBO x{self.player.combo_count}  (+{pct}%)",
                              True, (255, 210, 90))
            self.screen.blit(txt, (config.SCREEN_WIDTH // 2 - txt.get_width() // 2, 96))

    def _draw_region_atmosphere(self):
        if self.current_extension_id is None:
            return
        ext = self.world.get_extension(self.current_extension_id)
        if ext is None:
            return
        tint = self._region_tint(ext.spec)
        if tint is None:
            return
        overlay = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        overlay.set_alpha(30)
        overlay.fill(tint)
        self.screen.blit(overlay, (0, 0))

    # ---------------------------------------------------------------- CHANGED
    def _region_tint(self, spec):
        names = []
        for m in spec.world_modifiers:
            if not isinstance(m, dict):
                continue
            if m.get("_removed"):
                continue
            names.append(m.get("name", ""))
        flavor = " ".join(names).lower()

        if "chaos" in flavor or "corrupt" in flavor:
            return (120, 40, 160)
        if "storm" in flavor or "conductive" in flavor:
            return (40, 90, 170)
        if "ash" in flavor or "fire" in flavor:
            return (170, 70, 30)
        if "frozen" in flavor or "cold" in flavor or "glacier" in flavor:
            return (60, 120, 160)
        if "bone" in flavor or "brittle" in flavor:
            return (150, 140, 110)
        if spec.is_anomaly:
            return (180, 40, 60)
        return None