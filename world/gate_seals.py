# [PROJECT: world]
# NEW FILE: world/gate_seals.py
import math

SEAL_BOSS         = "boss"
SEAL_SHRINES      = "shrines"
SEAL_ELITE_HUNT   = "elite_hunt"
SEAL_PURGE        = "purge"
SEAL_SURVIVAL     = "survival"
SEAL_TRIAL        = "trial"
SEAL_BARGAIN      = "bargain"
SEAL_EXPLORATION  = "exploration"
SEAL_HUNT_NEMESIS = "hunt_nemesis"
SEAL_CORRUPTION   = "corruption"

SEAL_DESTROY_SPAWNERS = "destroy_spawners"
SEAL_HUNT_CONVOY      = "hunt_convoy"

SEAL_LABELS = {
    SEAL_BOSS: "Guardian", SEAL_SHRINES: "Shrine Ritual",
    SEAL_ELITE_HUNT: "Elite Hunt", SEAL_PURGE: "Purge",
    SEAL_SURVIVAL: "Survival", SEAL_TRIAL: "Trial",
    SEAL_BARGAIN: "Bargain", SEAL_EXPLORATION: "Exploration",
    SEAL_HUNT_NEMESIS: "Nemesis Hunt", SEAL_CORRUPTION: "Corruption",
     SEAL_DESTROY_SPAWNERS: "Spawner Purge",
    SEAL_HUNT_CONVOY:      "Convoy Hunt"
}

SEAL_COLORS = {
    SEAL_BOSS: (200, 60, 60), SEAL_SHRINES: (150, 200, 255),
    SEAL_ELITE_HUNT: (255, 160, 80), SEAL_PURGE: (220, 100, 100),
    SEAL_SURVIVAL: (255, 200, 100), SEAL_TRIAL: (200, 180, 255),
    SEAL_BARGAIN: (140, 220, 140), SEAL_EXPLORATION: (180, 200, 160),
    SEAL_HUNT_NEMESIS: (200, 100, 220), SEAL_CORRUPTION: (180, 40, 200),
    SEAL_DESTROY_SPAWNERS: (220, 90, 90),
    SEAL_HUNT_CONVOY:      (220, 180, 60),
}

SEAL_WEIGHTS = {
    SEAL_BOSS: 35, SEAL_SHRINES: 12, SEAL_ELITE_HUNT: 12,
    SEAL_PURGE: 10, SEAL_SURVIVAL: 8, SEAL_TRIAL: 8,
    SEAL_BARGAIN: 5, SEAL_EXPLORATION: 5,
    SEAL_HUNT_NEMESIS: 3, SEAL_CORRUPTION: 2,
    SEAL_DESTROY_SPAWNERS: 6,
    SEAL_HUNT_CONVOY:      5,
}

GATE_CURSES = {
    "drain":   "The gate drinks 1 HP/s within ~4 tiles.",
    "empower": "Enemies in this region have +20% HP.",
    "frenzy":  "Enemies in this region attack 15% faster.",
}
CURSE_CHANCE = 0.25


def roll_seal_type(rng, area_level=1, is_starting=False):
    if is_starting:
        return SEAL_BOSS  # starting gate = always the guardian
    pool, weights = [], []
    for seal, w in SEAL_WEIGHTS.items():
        if seal == SEAL_HUNT_NEMESIS and area_level < 3:
            continue
        if seal == SEAL_CORRUPTION and area_level < 5:
            continue
        if seal == SEAL_DESTROY_SPAWNERS and area_level < 3:
            continue
        if seal == SEAL_HUNT_CONVOY and area_level < 2:
            continue
        pool.append(seal)
        weights.append(w)
    return rng.choices(pool, weights=weights, k=1)[0]


def build_seal(seal_type, area_level, rng, is_starting=False,target_boss=None):
    seal = {
        "type": seal_type, "label": SEAL_LABELS[seal_type],
        "progress": 0, "target": 1, "ready": False,
        "timer": None, "timer_max": None, "wave_timer": 0.0,
        "curse": None, "curse_label": None,
    }

    if seal_type == SEAL_BOSS:
        seal["desc"] = "Defeat the region guardian."
        seal["target"] = 1
        if target_boss:
            seal["target_boss"] = target_boss
            seal["desc"] = f"Defeat {target_boss}."
    elif seal_type == SEAL_SHRINES:
        n = min(4, 2 + area_level // 4)
        seal["target"] = n
        seal["desc"] = f"Activate {n} shrines in this region."
    elif seal_type == SEAL_ELITE_HUNT:
        n = min(8, 3 + area_level // 3)
        seal["target"] = n
        seal["desc"] = f"Slay {n} elites in this region."
    elif seal_type == SEAL_PURGE:
        n = min(30, 12 + area_level * 2)
        seal["target"] = n
        seal["desc"] = f"Purge {n} enemies in this region."
    elif seal_type == SEAL_SURVIVAL:
        n = 2 + min(3, area_level // 4)
        seal["target"] = n
        seal["desc"] = f"Survive {n} waves at the gate."
    elif seal_type == SEAL_TRIAL:
        n = 6 + area_level
        t = 30.0 + area_level * 2
        seal["target"] = n
        seal["timer_max"] = t
        seal["timer"] = t
        seal["desc"] = f"Slay {n} enemies within {t:.0f}s."
    elif seal_type == SEAL_BARGAIN:
        n = 1 + area_level // 4
        seal["target"] = n
        seal["desc"] = f"Feed the gate {n} Extension Stones."
    elif seal_type == SEAL_EXPLORATION:
        n = 0.35
        seal["target"] = n
        seal["desc"] = f"Explore {n*100:.0f}% of the region."
    elif seal_type == SEAL_HUNT_NEMESIS:
        seal["target"] = 1
        seal["desc"] = "Slay the Nemesis guarding this gate."
    elif seal_type == SEAL_CORRUPTION:
        n = 15 + area_level * 2
        seal["target"] = n
        seal["desc"] = f"Survive corruption: slay {n} while the gate drains you."
    elif seal_type == SEAL_DESTROY_SPAWNERS:
        #n = 2 + area_level // 5
        n = min(5, 2 + area_level // 8)
        seal["target"] = n
        seal["desc"] = f"Destroy {n} enemy spawners in this region."
    elif seal_type == SEAL_HUNT_CONVOY:
        seal["target"] = 1
        seal["desc"] = "Intercept the enemy convoy before it escapes."

    if not is_starting and rng.random() < CURSE_CHANCE:
        curse = rng.choice(list(GATE_CURSES.keys()))
        seal["curse"] = curse
        seal["curse_label"] = GATE_CURSES[curse]

    return seal


def seal_progress_text(seal):
    t = seal["type"]
    if t == SEAL_EXPLORATION:
        p = seal.get("progress", 0.0)
        return f"{p*100:.0f}% / {seal['target']*100:.0f}%"
    if t == SEAL_TRIAL:
        remaining = max(0.0, seal.get("timer") or 0.0)
        return f"{seal['progress']}/{seal['target']}  ({remaining:.0f}s)"
    return f"{seal['progress']}/{seal['target']}"


def seal_is_auto_ticked(seal_type):
    return seal_type in (SEAL_EXPLORATION, SEAL_BARGAIN, SEAL_TRIAL,
                         SEAL_SURVIVAL, SEAL_CORRUPTION, SEAL_SHRINES)


def seal_counts_kills(seal_type):
    return seal_type in (SEAL_PURGE, SEAL_ELITE_HUNT, SEAL_SURVIVAL,
                         SEAL_TRIAL, SEAL_CORRUPTION,
                         SEAL_DESTROY_SPAWNERS, SEAL_HUNT_CONVOY)

KILL_SEAL_SHARE = {SEAL_PURGE: 0.8, SEAL_TRIAL: 0.6, SEAL_CORRUPTION: 0.75}


def describe_seal(seal):
    t, n = seal["type"], seal.get("target")
    if t == SEAL_BOSS:
        return f"Defeat {seal['target_boss']}." if seal.get("target_boss") else "Defeat the region guardian."
    if t == SEAL_SHRINES:
        return f"Activate {n} shrines in this region."
    if t == SEAL_ELITE_HUNT:
        return f"Slay {n} elites in this region."
    if t == SEAL_PURGE:
        return f"Purge {n} enemies in this region."
    if t == SEAL_SURVIVAL:
        return f"Survive {n} waves at the gate."
    if t == SEAL_TRIAL:
        return f"Slay {n} enemies within {seal.get('timer_max') or 0:.0f}s."
    if t == SEAL_BARGAIN:
        return f"Feed the gate {n} Extension Stones."
    if t == SEAL_EXPLORATION:
        return f"Explore {n * 100:.0f}% of the region."
    if t == SEAL_HUNT_NEMESIS:
        return "Slay the Nemesis guarding this gate."
    if t == SEAL_CORRUPTION:
        return f"Survive corruption: slay {n} while the gate drains you."
    if t == SEAL_DESTROY_SPAWNERS:
        return f"Destroy {n} enemy spawners in this region."
    if t == SEAL_HUNT_CONVOY:
        return "Intercept the enemy convoy before it escapes."
    return seal.get("desc", "")


def calibrate_seal(seal, enemies, elites, spawners=None, interactables=None):
    """Lower a seal's target to what the region can actually provide. Never raises it."""
    t = seal.get("type")
    old = seal.get("target", 1)
    new = old
    if t in KILL_SEAL_SHARE:
        new = min(old, max(1, int(round(enemies * KILL_SEAL_SHARE[t]))))
    elif t == SEAL_ELITE_HUNT:
        new = max(1, min(old, elites))
    elif t == SEAL_DESTROY_SPAWNERS and spawners is not None:
        new = max(1, min(old, spawners))
    elif t == SEAL_SHRINES and interactables is not None:
        new = max(1, min(old, interactables))
    if new != old:
        seal["target"] = new
        seal["calibrated_from"] = old
        seal["desc"] = describe_seal(seal)
    return seal