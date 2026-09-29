# core/rng.py
import random
import hashlib


class RNGService:
    def __init__(self, world_seed: int):
        self.world_seed = world_seed
        self._streams = {}
        self.get_stream("world")

    @staticmethod
    def _derive_seed(world_seed: int, name: str) -> int:
        # Deterministic across processes/runs (unlike hash()).
        digest = hashlib.sha256(f"{world_seed}:{name}".encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") & 0xFFFFFFFF

    def get_stream(self, name: str) -> random.Random:
        if name not in self._streams:
            derived = self._derive_seed(self.world_seed, name)
            self._streams[name] = random.Random(derived)
        return self._streams[name]

    def derive_child(self, parent_stream: str, child_key) -> "random.Random":
        name = f"{parent_stream}:{child_key}"
        return self.get_stream(name)