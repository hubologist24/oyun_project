# [PROJECT: save]
# [SYMBOLS] classes: SaveManager
import json
import os
import re
import time

import core.config as config


class SaveManager:
    """Per-slot save manager.

    Layout:
        saves/slot_<id>/state.json     - full game state
        saves/slot_<id>/stash.json     - per-character stash
        saves/slot_<id>/meta.json      - summary shown in character select

    Every character has its own directory, so multiple characters never
    clobber each other.
    """

    def __init__(self, slot_id="1", saves_dir=None):
        self.slot_id = str(slot_id)
        self.saves_dir = saves_dir or config.SAVES_DIR
        os.makedirs(self.saves_dir, exist_ok=True)

    # ------------------------------------------------------------------ paths
    def _slot_dir(self, slot_id=None):
        return os.path.join(self.saves_dir, f"slot_{slot_id or self.slot_id}")

    def _state_path(self):
        return os.path.join(self._slot_dir(), "state.json")

    def _stash_path(self):
        #return os.path.join(self._slot_dir(), "stash.json")
        return os.path.join(self.saves_dir, config.SHARED_STASH_FILE)

    def _meta_path(self):
        return os.path.join(self._slot_dir(), "meta.json")

    # ------------------------------------------------------------ save / load
    def save(self, game_state: dict):
        game_state["version"] = config.SAVE_VERSION
        game_state["saved_at"] = time.time()
        os.makedirs(self._slot_dir(), exist_ok=True)
        path = self._state_path()
        tmp  = path + ".tmp"                              # ← atomic
        with open(tmp, "w") as f:
            json.dump(game_state, f, indent=2)
        os.replace(tmp, path)
        self._update_meta(game_state)

    def load(self):
        path = self._state_path()
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[SaveManager] Failed to load '{path}': {e}")
            return None
        return data if isinstance(data, dict) else None

    def exists(self):
        return os.path.exists(self._state_path())

    def save_stash(self, stash):
        os.makedirs(self._slot_dir(), exist_ok=True)
        with open(self._stash_path(), "w") as f:
            json.dump(stash.to_dict(), f, indent=2)

    def load_stash(self):
        from items.stash import Stash
        path = self._stash_path()
        if not os.path.exists(path):
            return Stash.empty(cols=12, rows=10)
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return Stash.empty(cols=12, rows=10)
        return Stash.from_dict(data) if isinstance(data, dict) else Stash.empty(12, 10)

    # ------------------------------------------------------------------ meta
    def _update_meta(self, game_state: dict):
        player = game_state.get("player", {}) or {}
        world  = game_state.get("world", {}) or {}
        prog   = player.get("progression", {}) or {}
        meta = {
            "slot_id": self.slot_id,
            "name": self._read_meta().get("name", "Adventurer"),
            "level": int(prog.get("level", 1)),
            "playtime": float(world.get("total_playtime_seconds", 0.0)),
            "extensions": len(world.get("extensions", [])),
            "saved_at": game_state.get("saved_at", time.time()),
        }
        self._write_meta(meta)

    def _read_meta(self):
        path = self._meta_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_meta(self, meta):
        os.makedirs(self._slot_dir(), exist_ok=True)
        with open(self._meta_path(), "w") as f:
            json.dump(meta, f, indent=2)

    # ------------------------------------------------------- slot management
    @staticmethod
    def _sanitize(name: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9 _-]", "", name or "").strip()
        return (cleaned or "Adventurer")[:24]

    @classmethod
    def list_slots(cls, saves_dir=None):
        saves_dir = saves_dir or config.SAVES_DIR
        if not os.path.isdir(saves_dir):
            return []
        out = []
        for entry in os.listdir(saves_dir):
            if not entry.startswith("slot_"):
                continue
            slot_dir = os.path.join(saves_dir, entry)
            if not os.path.isdir(slot_dir):
                continue
            if not os.path.exists(os.path.join(slot_dir, "state.json")):
                continue
            meta = {}
            meta_path = os.path.join(slot_dir, "meta.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                except (json.JSONDecodeError, OSError):
                    meta = {}
            meta.setdefault("slot_id", entry[5:])
            meta.setdefault("name", "Adventurer")
            meta.setdefault("level", 1)
            meta.setdefault("playtime", 0.0)
            meta.setdefault("saved_at", 0.0)
            meta.setdefault("extensions", 0)
            out.append(meta)
        out.sort(key=lambda m: m.get("saved_at", 0), reverse=True)
        return out

    @classmethod
    def create_slot(cls, name: str, saves_dir=None):
        saves_dir = saves_dir or config.SAVES_DIR
        os.makedirs(saves_dir, exist_ok=True)
        used = set()
        for entry in os.listdir(saves_dir):
            if entry.startswith("slot_"):
                try:
                    used.add(int(entry[5:]))
                except ValueError:
                    pass
        next_id = 1
        while next_id in used:
            next_id += 1
        slot_id = str(next_id)
        slot_dir = os.path.join(saves_dir, f"slot_{slot_id}")
        os.makedirs(slot_dir, exist_ok=True)
        with open(os.path.join(slot_dir, "meta.json"), "w") as f:
            json.dump({
                "slot_id": slot_id,
                "name": cls._sanitize(name),
                "level": 1,
                "playtime": 0.0,
                "extensions": 0,
                "saved_at": time.time(),
            }, f, indent=2)
        return slot_id

    @classmethod
    def delete_slot(cls, slot_id, saves_dir=None):
        import shutil                                     # ← handle subdirs
        saves_dir = saves_dir or config.SAVES_DIR
        slot_dir = os.path.join(saves_dir, f"slot_{slot_id}")
        if not os.path.isdir(slot_dir):
            return False
        shutil.rmtree(slot_dir, ignore_errors=True)
        return not os.path.exists(slot_dir)