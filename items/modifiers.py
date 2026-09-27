"""
Loads prefix/suffix pools and affix-count probability tables from JSON.
Names are cosmetic (per spec); the `modifier` field is what actually
matters mechanically and maps directly into items/tiers.py's tier table.
"""
import json
import os

_BASE = os.path.join(os.path.dirname(__file__), "..", "data")


def _load(filename):
    with open(os.path.join(_BASE, filename), "r") as f:
        return json.load(f)


class ModifierPools:
    _prefixes = None
    _suffixes = None
    _affix_count_table = None
    _rarity_weights = None

    @classmethod
    def prefixes(cls):
        if cls._prefixes is None:
            cls._prefixes = _load("prefixes.json")["prefixes"]
        return cls._prefixes

    @classmethod
    def suffixes(cls):
        if cls._suffixes is None:
            cls._suffixes = _load("suffixes.json")["suffixes"]
        return cls._suffixes

    @classmethod
    def prefixes_for_slot(cls, slot):
        return [p for p in cls.prefixes() if slot in p["applies_to"]]

    @classmethod
    def suffixes_for_slot(cls, slot):
        return [s for s in cls.suffixes() if slot in s["applies_to"]]

    @classmethod
    def affix_count_table(cls):
        if cls._affix_count_table is None:
            cls._affix_count_table = _load("affix_count_probabilities.json")["brackets"]
        return cls._affix_count_table

    @classmethod
    def rarity_weights(cls):
        if cls._rarity_weights is None:
            cls._rarity_weights = _load("rarity_probabilities.json")
        return cls._rarity_weights

    @classmethod
    def roll_affix_counts(cls, item_level: int, rng):
        """Returns (num_prefixes, num_suffixes) based on data-driven brackets."""
        brackets = cls.affix_count_table()
        bracket = next(
            (b for b in brackets if b["min_item_level"] <= item_level <= b["max_item_level"]),
            brackets[-1]
        )
        dist = bracket["distribution"]
        weights = [entry["weight"] for entry in dist]
        chosen = rng.choices(dist, weights=weights, k=1)[0]
        return chosen["prefixes"], chosen["suffixes"]

    @classmethod
    def roll_rarity(cls, rng, force_min_affixes=0):
        weights = cls.rarity_weights()
        names = ["normal", "magic", "rare"]
        w = [weights[n] for n in names]
        if force_min_affixes >= 3:
            return "rare"
        rarity = rng.choices(names, weights=w, k=1)[0]
        return rarity