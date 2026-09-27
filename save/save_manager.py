"""
JSON-based save/load. Versioned so future schema changes (Phase 2+)
can migrate old saves instead of breaking them.
"""
import json
import os
import core.config as config


class SaveManager:
    def __init__(self, path=config.SAVE_FILE):
        self.path = path

    def save(self, game_state: dict):
        game_state["version"] = config.SAVE_VERSION
        with open(self.path, "w") as f:
            json.dump(game_state, f, indent=2)

    def load(self):
        if not os.path.exists(self.path):
            return None
        with open(self.path, "r") as f:
            data = json.load(f)
        return data

    def exists(self):
        return os.path.exists(self.path)