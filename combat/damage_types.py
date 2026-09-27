"""
Formal damage type registry. Loaded from data/damage_types.json so new
types can be added later (per spec) without touching Python code that
merely iterates "all known types".
"""
import json
import os

_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "damage_types.json")


class DamageTypes:
    _types = None

    @classmethod
    def load(cls):
        if cls._types is None:
            with open(_DATA_PATH, "r") as f:
                data = json.load(f)
            cls._types = data["damage_types"]
        return cls._types

    @classmethod
    def all(cls):
        return cls.load()

    @classmethod
    def is_valid(cls, dtype: str) -> bool:
        return dtype in cls.load()


PHYSICAL = "physical"
FIRE = "fire"
COLD = "cold"
LIGHTNING = "lightning"
CHAOS = "chaos"
POISON = "poison"
BLEED = "bleed"