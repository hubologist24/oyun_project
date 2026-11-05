from collections import Counter

ADAPT_THRESHOLD = 3


def _boost(target, speed_mult=1.0, behavior=None, resist=None, armor_flat=0):
    if speed_mult != 1.0 and hasattr(target, "speed"):
        target.speed = target.speed * speed_mult
    if behavior is not None and hasattr(target, "behavior"):
        target.behavior = behavior
    if hasattr(target, "resistances"):
        for k, v in (resist or {}).items():
            target.resistances[k] = target.resistances.get(k, 0.0) + v
        if armor_flat:
            target.resistances["armor_flat"] = \
                target.resistances.get("armor_flat", 0) + armor_flat


ADAPTATIONS = {
    "dash_strike": {
        "line": "Your dash is a habit. I counted.",
        "mutate": lambda t: _boost(t, speed_mult=1.45, behavior="chaser"),
    },
    "reverse_slash": {
        "line": "Turning your back will not save you.",
        "mutate": lambda t: _boost(t, resist={"physical": 0.30},
                                   armor_flat=25, behavior="ranged"),
    },
    "heavy_reversal": {
        "line": "Big swings. Small mind.",
        "mutate": lambda t: _boost(t, resist={"physical": 0.30},
                                   armor_flat=25, behavior="ranged"),
    },
    "retreat_shot": {
        "line": "Run, then. I am faster.",
        "mutate": lambda t: _boost(t, speed_mult=1.55, behavior="chaser"),
    },
    "perfect_range": {
        "line": "I know exactly how far you like me.",
        "mutate": lambda t: _boost(t, speed_mult=1.35, behavior="chaser"),
    },
    "intercept": {
        "line": "You chase momentum. I will circle it.",
        "mutate": lambda t: _boost(t, speed_mult=1.25, behavior="circler"),
    },
}


class NemesisLearning:
    """Persistently counts kinetic techniques the player uses.
    When a Nemesis meets a threshold for a technique it has not yet
    adapted to, it mutates itself accordingly."""

    def __init__(self, game):
        self.game = game
        self.counts = Counter()
        game.event_bus.subscribe("kinetic_technique", self._on_technique)

    def _on_technique(self, name=None, **kw):
        if name in ADAPTATIONS:
            self.counts[name] += 1

    def apply_to(self, nem, target):
        """Mutate `target` for each technique in `nem.adaptations` not yet applied.
        Returns a list of the flavour lines the nemesis should say."""
        newly = []
        for name, count in self.counts.items():
            if count < ADAPT_THRESHOLD:
                continue
            if name in nem.adaptations:
                continue
            try:
                ADAPTATIONS[name]["mutate"](target)
                nem.adaptations.append(name)
                newly.append(ADAPTATIONS[name]["line"])
            except Exception as e:
                print(f"[NemesisLearning] adaptation '{name}' failed: {e}")
        return newly