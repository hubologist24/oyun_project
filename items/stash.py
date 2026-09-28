"""
Stash: account-scoped, long-term item storage, independent of any
single character/world save. A brand-new character in a brand-new
world loads the SAME stash file.
"""
from typing import Optional, Tuple
from items.inventory import Inventory


class Stash:
    def __init__(self, cols: int = 12, rows: int = 10):
        self._inv = Inventory(cols=cols, rows=rows)

    @property
    def cols(self):
        return self._inv.cols

    @property
    def rows(self):
        return self._inv.rows

    @property
    def items(self):
        return self._inv.items

    def is_full(self) -> bool:
        return self._inv.is_full()

    def find_free_cell(self) -> Optional[Tuple[int, int]]:
        return self._inv.find_free_cell()

    def add_item(self, item, cell: Optional[Tuple[int, int]] = None) -> bool:
        return self._inv.add_item(item, cell=cell)

    def remove_item(self, item) -> None:
        self._inv.remove_item(item)

    def get_by_id(self, item_id):
        return self._inv.get_by_id(item_id)

    def get_at(self, cell: Tuple[int, int]):
        return self._inv.get_at(cell)

    def move_item(self, from_cell, to_cell) -> bool:
        return self._inv.move_item(from_cell, to_cell)

    def to_dict(self):
        return self._inv.to_dict()

    @staticmethod
    def from_dict(d):
        stash = Stash()
        stash._inv = Inventory.from_dict(d)
        return stash

    @staticmethod
    def empty(cols: int = 12, rows: int = 10):
        return Stash(cols=cols, rows=rows)
