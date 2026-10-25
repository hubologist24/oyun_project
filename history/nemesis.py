import random
from dataclasses import dataclass, asdict, fields ,field


FIRST = {
    "fire": ["Grix", "Cindra", "Ashmaw"], "cold": ["Vorn", "Isolde", "Hrimm"],
    "lightning": ["Zeph", "Voltaire", "Kraa"], "chaos": ["Nul", "Xhaz", "Ormund"],
    "physical": ["Brakk", "Mogul", "Thorne"], "poison": ["Sliss", "Venn"],
    "bleed": ["Rend", "Carmine"],
}
EPITHET = {
    "fire": "the Flame-Eater", "cold": "the Frostbound", "lightning": "the Stormcaller",
    "chaos": "the Voidtouched", "physical": "the Bonebreaker", "poison": "the Plaguebearer",
    "bleed": "the Red Hunger",
}
TAUNTS = {
    "fire": "Your fire was weak, wanderer.", "cold": "Your cold bones... how fitting.",
    "lightning": "Slow. So slow for a bringer of storms.",
    "chaos": "Chaos? I AM chaos, little spark.", "physical": "Is that all your arm can do?",
    "poison": "You taste of fear.", "bleed": "I enjoyed watching you bleed.",
}
REPEAT = ["Again? I was hoping for a challenge.", "I remember your scent.",
          "Every death makes me stronger."]
ROMAN = ["", "", " II", " III", " IV", " V"]


PERSONALITIES = {
    "aggressive": {"display": "the Relentless", "speed_mult": 1.20, "damage_mult": 1.15},
    "patient":    {"display": "the Patient",    "hp_mult":   1.30, "speed_mult": 0.85},
    "cunning":    {"display": "the Cunning",    "damage_mult": 1.10, "resist_bonus": 0.10},
    "frenzied":   {"display": "the Frenzied",   "speed_mult": 1.15, "damage_mult": 1.20, "hp_mult": 0.85},
    "resilient":  {"display": "the Unbroken",   "hp_mult":   1.40, "speed_mult": 0.90},
}

def pick_personality(rng):
    return rng.choice(list(PERSONALITIES.keys()))

def personality_taunt(personality, rng):
    lines = {
        "aggressive": ["No mercy this time!", "I'll tear you apart!"],
        "patient":    ["I've waited for this moment...", "Your time will come, wanderer."],
        "cunning":    ["A trap, and you walked right in.", "Predictable, as always."],
        "frenzied":   ["BLOOD! BLOOD!", "FASTER! FASTER!"],
        "resilient":  ["You cannot break me.", "I have endured worse than you."],
    }
    return rng.choice(lines.get(personality, ["You cannot win."]))


def make_name(dtype, rng):
    return f"{rng.choice(FIRST.get(dtype, FIRST['physical']))}, {EPITHET.get(dtype, 'the Unyielding')}"


def taunt_for(dtype, kills, rng):
    if kills >= 3:
        return "Three times you fell. Now the realm kneels."
    if kills == 2:
        return rng.choice(REPEAT)
    return TAUNTS.get(dtype, "You are mine now.")


@dataclass
class Nemesis:
    nid: int = 0
    name: str = ""
    damage_type: str = "physical"
    taunt: str = ""
    behavior: str = "chaser"
    area_level: int = 1
    kills_on_player: int = 1
    generation: int = 1
    world_boss: bool = False
    archetype: str = "dash_predator"
    status: str = "alive"        # alive | pending | dead
    respawn_at: float = 0.0
    grudge: int = 0                       # kills since last evolution (forces evolve at 3)
    escapes: int = 0                      # times this nemesis fled a fight
    personality: str = "aggressive"       # key into PERSONALITIES

    stolen_item: dict = field(default_factory=dict)

    def short(self):
        return self.name.split(",")[0]

    def display(self):
        return self.short() + ROMAN[min(self.generation, 5)]

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        names = {f.name for f in fields(Nemesis)}
        return Nemesis(**{k: v for k, v in d.items() if k in names})


class NemesisRegistry:
    def __init__(self):
        self.records = []

    def add(self, n):
        n.nid = len(self.records) + 1
        self.records.append(n)

    def to_dict(self):
        return {"records": [r.to_dict() for r in self.records]}

    @staticmethod
    def from_dict(d):
        reg = NemesisRegistry()
        reg.records = [Nemesis.from_dict(r) for r in (d or {}).get("records", [])]
        return reg