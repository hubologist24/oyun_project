"""
Debug/dev screen: F6 = Show Player Profile.
Views (TAB cycles): profile -> build -> codex.
 - profile: aggregated PlayerProfile (the payload handed to the AI generators)
 - build:   perks, keystones, technique mastery
 - codex:   account-wide achievements (scrollable: UP/DOWN or mouse wheel)
"""
import pygame
import core.config as config

VIEWS = ["profile", "build", "codex"]


class ProfileScreen:
    def __init__(self):
        self.visible = False
        self.profile = None
        self.codex = None
        self.player = None
        self.view_mode = "profile"
        self.scroll = 0
        self.font_title = pygame.font.SysFont("consolas", 26, bold=True)
        self.font_body = pygame.font.SysFont("consolas", 18)
        self.font_small = pygame.font.SysFont("consolas", 14)

    # ---------------- control ----------------
    def show(self, profile, codex=None, player=None):
        self.profile = profile
        if codex is not None:
            self.codex = codex
        if player is not None:
            self.player = player
        self.scroll = 0
        self.visible = True

    def hide(self):
        self.visible = False

    def toggle_view(self):
        i = VIEWS.index(self.view_mode) if self.view_mode in VIEWS else 0
        self.view_mode = VIEWS[(i + 1) % len(VIEWS)]
        self.scroll = 0

    def scroll_by(self, delta):
        self.scroll = max(0, self.scroll + delta)

    # ---------------- helpers ----------------
    def _title(self, surface, text, color):
        t = self.font_title.render(text, True, color)
        surface.blit(t, (80, 50))
        return 100

    def _line(self, surface, text, y, color=(220, 220, 225), font=None, x=80):
        surface.blit((font or self.font_body).render(text, True, color), (x, y))

    # ---------------- draw ----------------
    def draw(self, surface):
        if not self.visible or self.profile is None:
            return
        overlay = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
        overlay.set_alpha(235)
        overlay.fill((10, 10, 14))
        surface.blit(overlay, (0, 0))

        if self.view_mode == "codex" and self.codex is not None:
            self._draw_codex(surface)
        elif self.view_mode == "build" and self.player is not None:
            self._draw_build(surface)
        else:
            self._draw_profile(surface)

    def _draw_profile(self, surface):
        y = self._title(surface, "PLAYER PROFILE  (TAB: next view | F6/ESC: close)",
                        (255, 220, 120))
        d = self.profile.to_dict()
        fb = d.get("feedback") or {}
        lines = [
            f"Level: {d['level']}",
            f"Main Damage: {d['main_damage']}    Secondary: {d['secondary_damage']}",
            f"Favorite Skill: {d['favorite_skill']}    Most Used Weapon: {d['most_used_weapon']}",
            f"Favorite Technique: {d['favorite_technique']}",
            f"Defensive Strength: {d['defensive_strength']}",
            "",
            f"Deaths (by cause): {d['deaths']}    Total: {d['total_deaths']}",
            f"Boss Attempts: {d['boss_attempts']}    Boss Successes: {d['boss_successes']}",
            "",
            f"Exploration (current area): {d['exploration'] * 100:.1f}%",
            f"Exploration (average): {d['average_exploration'] * 100:.1f}%",
            "",
            f"Rare Items Found: {d['rare_items_found']}    Unique Items Found: {d['unique_items_found']}",
            f"Six-Mod (3P/3S) Items Found: {d['six_mod_items_found']}",
            f"Items Kept: {d['items_kept']}    Items Discarded: {d['items_discarded']}",
            "",
            f"Melee/Ranged Preference: {d['melee_ranged_preference']}",
            f"Average Combat Distance: {d['average_combat_distance']} px",
            f"Nemesis Trophies: {', '.join(d['trophies']) or 'none'}",
            "",
            f"Playtime: {d['playtime_seconds']:.0f}s    Extensions Generated: {d['extensions_generated']}",
            "",
            f"World feedback -> too hard: {fb.get('too_hard', 0)}   "
            f"too easy: {fb.get('too_easy', 0)}   "
            f"avoided biomes: {', '.join(fb.get('avoid_biomes', [])) or 'none'}",
        ]
        for line in lines:
            self._line(surface, line, y)
            y += 26

    def _draw_build(self, surface):
        from player.perks import MINOR_PERKS, KEYSTONES
        from combat.kinetic import TECHNIQUES
        from history.codex import MASTERY_THRESHOLDS
        p = self.player
        y = self._title(surface, "BUILD  (TAB: next view | F6/ESC: close)", (140, 230, 255))

        # --- keystones ---
        self._line(surface, "KEYSTONES", y, (255, 210, 110)); y += 28
        chosen = set(p.keystones)
        active = p.active_keystones()
        if not active:
            self._line(surface, "  none yet - you earn one every 10th level", y,
                       (150, 150, 160), self.font_small); y += 20
        for kid in sorted(active):
            ks = KEYSTONES.get(kid)
            if not ks:
                continue
            src = "" if kid in chosen else "  (from equipped unique)"
            self._line(surface, f"  * {ks['name']}{src}", y, (255, 230, 150)); y += 22
            self._line(surface, "      " + ks["desc"], y, (170, 170, 180), self.font_small)
            y += 20
        y += 10

        # --- perks ---
        self._line(surface, "PERKS", y, (120, 220, 255)); y += 28
        if not p.perks:
            self._line(surface, "  none yet", y, (150, 150, 160), self.font_small); y += 20
        for pid, n in sorted(p.perks.items(), key=lambda kv: -kv[1]):
            perk = MINOR_PERKS.get(pid)
            if not perk:
                continue
            self._line(surface, f"  {perk['name']} x{n}   {perk['desc']}", y,
                       (210, 210, 220), self.font_small)
            y += 20
        y += 10

        # --- technique mastery ---
        self._line(surface, "TECHNIQUE MASTERY  (account-wide)", y, (150, 255, 170)); y += 28
        if self.codex is None:
            return
        for tid, (label, color) in TECHNIQUES.items():
            uses = self.codex.technique_uses.get(tid, 0)
            tier = self.codex.mastery_tier(tid)
            if tier < len(MASTERY_THRESHOLDS):
                nxt = f"next at {MASTERY_THRESHOLDS[tier]}"
            else:
                nxt = "MAX"
            known = tid in self.codex.techniques_discovered
            col = color if known else (110, 110, 120)
            txt = f"  {label:<16} uses {uses:<5} tier {tier}/{len(MASTERY_THRESHOLDS)}  ({nxt})"
            if not known:
                txt = f"  {label:<16} undiscovered"
            self._line(surface, txt, y, col, self.font_small)
            y += 20

    def _draw_codex(self, surface):
        from history.codex import ACHIEVEMENTS
        y = self._title(surface,
                        "THE LIVING CODEX  (TAB: next view | UP/DOWN/wheel: scroll | F6/ESC: close)",
                        (180, 220, 255))
        unlocked_count = len(self.codex.unlocked)
        self._line(surface,
                   f"Legacy Bonuses Unlocked: {unlocked_count} / {len(ACHIEVEMENTS)}",
                   y, (200, 200, 220))
        y += 40

        # Build all rows first, then clip to the screen using self.scroll.
        rows = []   # (text, color, font)
        for aid, ach in ACHIEVEMENTS.items():
            unlocked = aid in self.codex.unlocked
            current, target = self.codex.progress_for(aid)
            color = (240, 220, 120) if unlocked else (150, 150, 160)
            marker = "[x]" if unlocked else "[ ]"
            rows.append((f"{marker} {ach['name']}", color, self.font_body))
            rows.append(("    " + ach["description"], (170, 170, 180), self.font_small))
            if not unlocked and ach.get("target"):
                rows.append((f"    Progress: {current}/{target}", (140, 140, 160), self.font_small))
            if unlocked:
                bonus = ", ".join(f"+{v} {k}" for k, v in ach["bonus"].items())
                rows.append((f"    Bonus: {bonus}", (110, 230, 110), self.font_small))
            rows.append(("", (0, 0, 0), self.font_small))

        visible_rows = max(1, (config.SCREEN_HEIGHT - 60 - y) // 20)
        self.scroll = min(self.scroll, max(0, len(rows) - visible_rows))
        for text, color, font in rows[self.scroll:self.scroll + visible_rows]:
            if text:
                surface.blit(font.render(text, True, color), (80, y))
            y += 20