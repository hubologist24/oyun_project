"""
Simple list-backed inventory. Phase 1 has no size cap or grid layout.
"""


class Inventory:
    def __init__(self, capacity: int = 40):
        self.items = []
        self.capacity = capacity

    def add_item(self, item) -> bool:
        if len(self.items) >= self.capacity:
            return False
        self.items.append(item)
        return True

    def remove_item(self, item):
        if item in self.items:
            self.items.remove(item)

    def get_by_id(self, item_id):
        for it in self.items:
            if it.item_id == item_id:
                return it
        return None