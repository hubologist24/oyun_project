"""Level-up perks (minor, every level) and keystones (every 10 levels)."""

MINOR_PERKS = {
    "vigor":     {"name": "Vigor",      "desc": "+15 Max HP, +2 Armor",          "bonus": {"max_hp": 15, "armor": 2}},
    "power":     {"name": "Power",      "desc": "+4 Base Damage",                "bonus": {"base_damage": 4}},
    "tempo":     {"name": "Tempo",      "desc": "+8% Attack Speed",              "bonus": {"attack_speed_bonus": 0.08}},
    "keen":      {"name": "Keen Eye",   "desc": "+3% Critical Chance",           "bonus": {"crit_chance": 0.03}},
    "swiftness": {"name": "Swiftness",  "desc": "+6% Move Speed, +2% Crit",      "bonus": {"speed_pct": 6, "crit_chance": 0.02}},
    "bulwark":   {"name": "Bulwark",    "desc": "+5 Armor, +10 Max HP",          "bonus": {"armor": 5, "max_hp": 10}},
    "warding":   {"name": "Warding",    "desc": "+3% Fire/Cold/Lightning/Chaos resist", "bonus": {"resist_all": 0.03}},
}

KEYSTONES = {
    "crit_echo":     {"name": "Echoing Strikes", "desc": "Critical hits echo 0.4s later for 40% damage."},
    "rhythm":        {"name": "Rhythm of Steel", "desc": "Every 5th consecutive melee hit leaves enemies Exposed (guaranteed reaction)."},
    "executioner":   {"name": "Executioner",     "desc": "+50% damage against enemies below 30% HP."},
    "phantom_dash":  {"name": "Phantom Dash",    "desc": "+1 dash charge and dash recharges 25% faster."},
    "resonance":     {"name": "Resonance",       "desc": "Elemental reactions repeat at 50% power 0.5s later."},
    "glass_cannon":  {"name": "Glass Cannon",    "desc": "+35% base damage, -25% max HP."},
}


def _offer(table, ids, rng, n):
    ids = sorted(ids)
    picks = rng.sample(ids, min(n, len(ids)))
    return [{"id": i, "name": table[i]["name"], "desc": table[i]["desc"]} for i in picks]


def roll_minor_offer(rng, n=3):
    return _offer(MINOR_PERKS, MINOR_PERKS.keys(), rng, n)


def roll_keystone_offer(owned, rng, n=3):
    pool = [k for k in KEYSTONES if k not in owned]
    return _offer(KEYSTONES, pool, rng, n) if pool else []


def apply_perks_to_stats(player, stats):
    """Idempotent: effective stats are rebuilt from base on every recalc."""
    for pid, count in player.perks.items():
        perk = MINOR_PERKS.get(pid)
        if not perk:
            continue
        for k, v in perk["bonus"].items():
            v = v * count
            if k == "max_hp":
                stats.max_hp += int(v)
            elif k == "armor":
                stats.armor += int(v)
            elif k == "base_damage":
                stats.base_damage += int(v)
            elif k == "crit_chance":
                stats.crit_chance += v
            elif k == "attack_speed_bonus":
                stats.attack_speed_bonus += v
            elif k == "speed_pct":
                stats.speed *= (1.0 + v / 100.0)
            elif k == "resist_all":
                for d in ("fire", "cold", "lightning", "chaos"):
                    stats.resistances[d] = stats.resistances.get(d, 0.0) + v

    if "glass_cannon" in player.active_keystones():
        stats.base_damage = int(stats.base_damage * 1.35)
        stats.max_hp = max(1, int(stats.max_hp * 0.75))