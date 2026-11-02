import json
import os

_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "uniques.json")
_cache = None


def all_uniques():
    global _cache
    if _cache is None:
        with open(_PATH, "r") as f:
            _cache = {u["id"]: u for u in json.load(f)["uniques"]}
    return _cache


def get_unique(uid):
    return all_uniques().get(uid)


def eligible_uniques(area_level):
    return [u for u in all_uniques().values() if u.get("min_level", 1) <= area_level + 2]