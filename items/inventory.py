"""
Grid-based inventory - Phase 8.

Replaces the flat capacity-limited list with a sparse (col, row) -> item
mapping, per spec section 3 ("Finite Inventory Slot System (grid or
fixed slot limits)").

Backward compatibility: `.items` remains a property returning items in
a stable reading order (top-left to bottom-right), so existing callers
(core/game.py's _auto_equip_best/_equip_action, ui/hud.py's
InventoryUI, save/load) keep working without modification.
"""
from typing import Optional, Tuple, Dict


"""
Grid-based inventory - Phase 8.

Sparse (col, row) -> item_id mapping. `.items` remains available as a
stable reading-order (row-major) list for callers that don't care
about grid position (HUD panel, auto-equip scoring, etc.).
"""
from typing import Optional, Tuple, Dict


class Inventory:
    def __init__(self, cols: int = 10, rows: int = 6):
        self.cols = cols
        self.rows = rows
        self.grid: Dict[Tuple[int, int], int] = {}
        self.items_by_id: Dict[int, object] = {}

    @property
    def capacity(self) -> int:
        return self.cols * self.rows

    def is_full(self) -> bool:
        return len(self.grid) >= self.capacity

    def find_free_cell(self) -> Optional[Tuple[int, int]]:
        for r in range(self.rows):
            for c in range(self.cols):
                if (c, r) not in self.grid:
                    return (c, r)
        return None

    def add_item(self, item, cell: Optional[Tuple[int, int]] = None) -> bool:
        if cell is not None:
            if cell in self.grid or not (0 <= cell[0] < self.cols and 0 <= cell[1] < self.rows):
                return False
            target_cell = cell
        else:
            target_cell = self.find_free_cell()
            if target_cell is None:
                return False

        self.grid[target_cell] = item.item_id
        self.items_by_id[item.item_id] = item
        return True

    def remove_item(self, item) -> None:
        cell = self._cell_for_id(item.item_id)
        if cell is not None:
            del self.grid[cell]
        self.items_by_id.pop(item.item_id, None)

    def get_by_id(self, item_id):
        return self.items_by_id.get(item_id)

    def get_at(self, cell: Tuple[int, int]):
        item_id = self.grid.get(cell)
        return self.items_by_id.get(item_id) if item_id is not None else None

    def move_item(self, from_cell: Tuple[int, int], to_cell: Tuple[int, int]) -> bool:
        if from_cell not in self.grid:
            return False
        moving_id = self.grid[from_cell]
        displaced_id = self.grid.get(to_cell)
        del self.grid[from_cell]
        self.grid[to_cell] = moving_id
        if displaced_id is not None:
            self.grid[from_cell] = displaced_id
        return True

    def _cell_for_id(self, item_id: int) -> Optional[Tuple[int, int]]:
        for cell, iid in self.grid.items():
            if iid == item_id:
                return cell
        return None

    @property
    def items(self):
        ordered_cells = sorted(self.grid.keys(), key=lambda p: (p[1], p[0]))
        return [self.items_by_id[self.grid[c]] for c in ordered_cells]

    def to_dict(self):
        return {
            "cols": self.cols,
            "rows": self.rows,
            "cells": [
                {"col": c, "row": r, "item": self.items_by_id[iid].to_dict()}
                for (c, r), iid in self.grid.items()
            ],
        }

    @staticmethod
    def from_dict(d):
        from items.item import Item, _allocate_id
        inv = Inventory(cols=d.get("cols", 10), rows=d.get("rows", 6))
        seen_ids = set()
        for entry in d.get("cells", []):
            item = Item.from_dict(entry["item"])
            # B14: duplicate item_ids across cells would dangle. Reassign.
            if item.item_id in seen_ids:
                item.item_id = _allocate_id()
            seen_ids.add(item.item_id)
            cell = (entry["col"], entry["row"])
            inv.grid[cell] = item.item_id
            inv.items_by_id[item.item_id] = item
        return inv