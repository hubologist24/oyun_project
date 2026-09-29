# save/save_manager.py
import json
import os
import core.config as config

STASH_FILE = "stash.json"


class SaveManager:
    def __init__(self, path=config.SAVE_FILE, stash_path=STASH_FILE):
        self.path = path
        self.stash_path = stash_path

    def save(self, game_state: dict):
        game_state["version"] = config.SAVE_VERSION
        with open(self.path, "w") as f:
            json.dump(game_state, f, indent=2)

    def load(self):
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[SaveManager] Failed to load save file '{self.path}': {e}")
            return None
        if not isinstance(data, dict):
            print(f"[SaveManager] Save file '{self.path}' has unexpected "
                  f"top-level type {type(data).__name__}; ignoring.")
            return None
        return data

    def exists(self):
        return os.path.exists(self.path)

    def save_stash(self, stash):
        with open(self.stash_path, "w") as f:
            json.dump(stash.to_dict(), f, indent=2)

    def load_stash(self):
        from items.stash import Stash
        if not os.path.exists(self.stash_path):
            return Stash.empty(cols=12, rows=10)
        try:
            with open(self.stash_path, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[SaveManager] Failed to load stash file '{self.stash_path}': {e}")
            return Stash.empty(cols=12, rows=10)
        if not isinstance(data, dict):
            print(f"[SaveManager] Stash file '{self.stash_path}' has unexpected "
                  f"top-level type {type(data).__name__}; ignoring.")
            return Stash.empty(cols=12, rows=10)
        return Stash.from_dict(data)