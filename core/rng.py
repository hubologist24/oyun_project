"""
Deterministic RNG service.

Phase 1 only uses a single world_seed, but the API is already shaped
for the future world_seed -> extension_seed -> area_seed hierarchy
described in the design doc, so later phases don't need to change
call sites.
"""
import random


class RNGService:
    def __init__(self, world_seed: int):
        self.world_seed = world_seed
        self._streams = {}
        self.get_stream("world")

    def get_stream(self, name: str) -> random.Random:
        """Return (creating if needed) a named deterministic RNG stream."""
        if name not in self._streams:
            # Derive a sub-seed deterministically from the stream name.
            derived = hash((self.world_seed, name)) & 0xFFFFFFFF
            self._streams[name] = random.Random(derived)
        return self._streams[name]

    def derive_child(self, parent_stream: str, child_key) -> "random.Random":
        """
        Used later for extension_seed / area_seed derivation:
        e.g. derive_child('world', 'extension_1') -> new deterministic stream.
        """
        name = f"{parent_stream}:{child_key}"
        return self.get_stream(name)