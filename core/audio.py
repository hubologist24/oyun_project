"""
Tiny procedural SFX layer. No asset files, no numpy.

Synthesizes short PCM tones at init time. If the mixer is unavailable
(headless test, no audio device), every play() becomes a no-op so the
game never crashes on missing audio infrastructure.
"""
import math
import struct

import pygame

_SAMPLE_RATE = 22050
_sounds: dict = {}
_enabled = False
_initialized = False


def init():
    global _enabled, _initialized
    if _initialized:
        return
    _initialized = True
    try:
        pygame.mixer.init(frequency=_SAMPLE_RATE, size=-16, channels=1, buffer=256)
        _enabled = True
    except Exception as e:
        print(f"[audio] mixer unavailable, SFX disabled: {e}")
        _enabled = False
    _define_all()


def _make_tone(freq, duration_ms, volume=0.3, decay=10.0):
    n = int(_SAMPLE_RATE * duration_ms / 1000)
    if n <= 0:
        return b""
    out = []
    for i in range(n):
        t = i / _SAMPLE_RATE
        env = min(1.0, i / 80.0) * math.exp(-t * decay)
        v = int(32767 * volume * env * math.sin(2 * math.pi * freq * t))
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        out.append(struct.pack("<h", v))
    return b"".join(out)


def _ensure(name, freq, duration_ms, volume=0.3, decay=10.0):
    if name in _sounds:
        return
    if not _enabled:
        _sounds[name] = None
        return
    try:
        buf = _make_tone(freq, duration_ms, volume, decay)
        _sounds[name] = pygame.mixer.Sound(buffer=buf)
    except Exception as e:
        print(f"[audio] failed to build '{name}': {e}")
        _sounds[name] = None


def play(name):
    snd = _sounds.get(name)
    if snd is not None:
        try:
            snd.play()
        except Exception:
            pass


def _define_all():
    # Telegraph cues -- distinct pitch per attack type so the player
    # learns them by ear (low=heavy, high=fast).
    _ensure("telegraph_melee",  220, 160, 0.22, decay=12)
    _ensure("telegraph_dash",   130, 220, 0.28, decay=8)
    _ensure("telegraph_ranged", 340, 180, 0.18, decay=12)
    # Impact
    _ensure("hit_player",  90, 140, 0.35, decay=14)
    _ensure("hit_enemy",  500,  55, 0.14, decay=22)
    _ensure("hit_boss",   700,  70, 0.18, decay=18)
    # Progression
    _ensure("level_up",   880, 320, 0.22, decay=5)
    _ensure("evolution",  300, 600, 0.28, decay=3)