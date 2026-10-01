"""
Item tooltip rendering: name/rarity/slot/ilvl/affix list, optionally
with a stat-diff comparison against a second item (per spec section 3,
"Interactive Tooltip Comparison (On-Hover)").

Pure rendering module -- takes a surface + position + item(s), draws,
returns nothing. No game-state access, so it's trivially reusable from
both the inventory grid and the equipped-slots panel.
"""
import pygame
from items.comparison import compare_items, format_diff_line


class ItemTooltip:
    def __init__(self):
        self.font_title = pygame.font.SysFont("consolas", 16, bold=True)
        self.font_body = pygame.font.SysFont("consolas", 13)
        self.font_diff = pygame.font.SysFont("consolas", 13, bold=True)

    def draw(self, surface, item, x, y, compare_against=None, max_width=280):
        """
        Draws a tooltip box for `item` at (x, y) (top-left anchor).
        If `compare_against` is given (the currently-equipped item in
        that slot, or None if the slot is empty), a delta section is
        appended showing +/- stat changes per spec's example format
        ("+40 Cold Damage", "-30 Physical Damage", "+5 Armor").
        """
        if item is None:
            return

        lines = self._build_info_lines(item)
        diffs = compare_items(item, compare_against) if compare_against is not None or True else []
        diff_lines = [format_diff_line(d) for d in diffs]

        padding = 10
        line_h = 18
        total_lines = len(lines) + (len(diff_lines) + 2 if diff_lines else 0)
        box_h = padding * 2 + total_lines * line_h
        box_w = max_width

        # Keep on-screen
        import core.config as config
        if x + box_w > config.SCREEN_WIDTH:
            x = config.SCREEN_WIDTH - box_w - 8
        if y + box_h > config.SCREEN_HEIGHT:
            y = config.SCREEN_HEIGHT - box_h - 8

        box = pygame.Rect(x, y, box_w, box_h)
        pygame.draw.rect(surface, (22, 22, 28), box)
        pygame.draw.rect(surface, item.rarity_color(), box, 2)

        cy = y + padding
        for i, (text, color) in enumerate(lines):
            font = self.font_title if i == 0 else self.font_body
            surf = font.render(text, True, color)
            surface.blit(surf, (x + padding, cy))
            cy += line_h

        if diff_lines:
            cy += 4
            pygame.draw.line(surface, (70, 70, 80), (x + padding, cy), (x + box_w - padding, cy))
            cy += 10
            header = self.font_body.render("Compared to equipped:", True, (170, 170, 180))
            surface.blit(header, (x + padding, cy))
            cy += line_h
            for diff_text, is_positive in zip(diff_lines, [d["is_positive"] for d in diffs]):
                color = (110, 230, 110) if is_positive else (230, 100, 100)
                surf = self.font_diff.render(diff_text, True, color)
                surface.blit(surf, (x + padding, cy))
                cy += line_h

    def _build_info_lines(self, item):
        """Returns list of (text, color) tuples for the info section."""
        lines = [(item.display_name, item.rarity_color())]
        lines.append((f"{item.slot.title()}  -  {item.rarity.title()}  -  ilvl {item.item_level}",
                      (180, 180, 190)))
        from items.weapon_subtypes import WeaponSubtypes
        subtype = getattr(item, "weapon_subtype", None)
        if subtype:
            lines.append((f"Type: {WeaponSubtypes.display_name(subtype)}  "
                          f"({WeaponSubtypes.attack_profile(subtype).display_pattern})",
                          (200, 210, 220)))
        if item.implicit:
            lines.append((f"(implicit) {item.implicit.format_line()}", (160, 160, 170)))
        for p in item.prefixes:
            lines.append((f"{p.display_name}: {p.format_line()}", (150, 190, 255)))
        for s in item.suffixes:
            lines.append((f"{s.display_name}: {s.format_line()}", (170, 255, 190)))
        ctx = getattr(item, "creation_context", None)
        if ctx:
            ext_id = ctx.get("extension_id", "unknown")
            origin = ("the Starting Region" if ext_id == "starting_world"
                      else ext_id.replace("_", " ").title())
            lines.append((
                f"Forged in {origin} (area lvl {ctx.get('area_level', '?')})",
                (150, 140, 120)))

        # --- vintage: affixes whose tier table has since changed ---
        from items.tiers import get_tier_table
        table = get_tier_table(None)
        shown_vintage = False
        for affix in item.all_affixes():
            mod = affix.modifier
            if mod not in table or affix.tier not in table.get(mod, {}):
                continue
            current = table[mod][affix.tier]
            created = affix.creation_tier_range
            if created and list(current) != list(created):
                if not shown_vintage:
                    lines.append(("VINTAGE -- this item predates the world's changes:", (230, 190, 90)))
                    shown_vintage = True
                lines.append((
                    f"  {affix.tier} {mod.replace('_', ' ')}: rolled [{created[0]}-{created[1]}], "
                    f"world now [{current[0]}-{current[1]}]",
                    (230, 190, 90)))
        if shown_vintage:
            lines.append(("This item will never fall behind the history of the world.", (240, 210, 120)))
        return lines