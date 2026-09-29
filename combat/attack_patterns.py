"""
Attack pattern resolution. This module is the SINGLE place where a
weapon subtype turns into a concrete attack: arc targets, projectiles,
or cone AoE. It contains no item-stat logic and no world-rule logic --
it just answers "which enemies are hit / what projectile is spawned".

Player attributes are deliberately NOT consulted here; the equipped
weapon's subtype is the only input that decides the attack type.
"""
import math
import pygame

from items.weapon_subtypes import AttackProfile, WeaponSubtypes


DEFAULT_UNARMED_PROFILE = AttackProfile(
    kind="arc",
    range=42,
    arc_degrees=80,
    hits_all=True,
    attack_cooldown_multiplier=1.0,
    damage_multiplier=0.7,
    display_pattern="unarmed",
)


def resolve_attack_profile(player) -> AttackProfile:
    """
    Returns the AttackProfile for the player's current weapon.
    Falls back to a bare-fist profile if no weapon is equipped.
    """
    weapon = player.equipped.get("weapon")
    subtype = getattr(weapon, "weapon_subtype", None) if weapon else None
    if subtype and WeaponSubtypes.is_valid(subtype):
        return WeaponSubtypes.attack_profile(subtype)
    return DEFAULT_UNARMED_PROFILE


def find_arc_targets(player, targets, profile: AttackProfile):
    """
    Returns targets inside an arc-shaped hit region centered on the
    player's facing direction, ordered from nearest to furthest.
    Used for both 'arc' (sword/greatsword) and 'cone_aoe' (long wand)
    patterns -- the only difference is arc_degrees / range.
    """
    fx, fy = player.facing.x, player.facing.y
    if fx == 0 and fy == 0:
        return []

    facing_angle = math.atan2(fy, fx)
    half_arc = math.radians(profile.arc_degrees / 2.0)
    reach = profile.range

    hits = []
    for t in targets:
        if not getattr(t, "alive", True):
            continue
        half_w = max(getattr(t, "width", 28), getattr(t, "height", 28)) / 2.0
        dx = t.x - player.x
        dy = t.y - player.y
        dist = math.hypot(dx, dy)
        if dist > reach + half_w:
            continue
        if dist < 1e-3:
            hits.append((0.0, t))
            continue
        angle = math.atan2(dy, dx)
        diff = abs(((angle - facing_angle + math.pi) % (2 * math.pi)) - math.pi)
        if diff <= half_arc:
            hits.append((dist, t))

    hits.sort(key=lambda pair: pair[0])
    return [t for _, t in hits]


class PlayerProjectile:
    """A travelling projectile spawned by bow / wand subtypes."""

    def __init__(self, x, y, vx, vy, damage_type, damage_multiplier,
                 world_rules, piercing, max_range, radius=6):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.damage_type = damage_type
        self.damage_multiplier = damage_multiplier
        self.world_rules = world_rules
        self.piercing = piercing
        self.remaining_range = max_range
        self.radius = radius
        self.alive = True

    @property
    def rect(self) -> pygame.Rect:
        return pygame.Rect(int(self.x - self.radius), int(self.y - self.radius),
                           self.radius * 2, self.radius * 2)

    def update(self, dt: float):
        step = math.hypot(self.vx, self.vy) * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.remaining_range -= step
        if self.remaining_range <= 0:
            self.alive = False


def spawn_projectile(player, profile: AttackProfile, damage_type: str, world_rules):
    speed = profile.projectile_speed
    vx = player.facing.x * speed
    vy = player.facing.y * speed
    return PlayerProjectile(
        x=player.x,
        y=player.y,
        vx=vx,
        vy=vy,
        damage_type=damage_type,
        damage_multiplier=profile.damage_multiplier,
        world_rules=world_rules,
        piercing=profile.piercing,
        max_range=profile.range,
    )