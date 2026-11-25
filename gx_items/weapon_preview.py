"""Native ITM glow emitters and Weapon.Seff enchantment recipes."""

from dataclasses import dataclass
from functools import lru_cache
from math import ceil, cos, isfinite, sin
from struct import Struct

import numpy as np

from .equipment_models import Cursor, I32, ModelFormatError, WEAPON_TYPES, basename
from .effect_preview import (
    EffectMaterial, EffectPreviewRenderer, IDENTITY, MAX_EMITTERS, QUAD, QUAD_FACES, QUAD_UV, project_particle,
)

SEFF_FIELDS = Struct("<IfIIIf")
SEFF_PLACEMENT = Struct("<3B5fB3f")
GLOW_FILES = ("item_L.EFT", "item_F.EFT", "item_E.EFT", "item_W.EFT")
SEFF_FILE = "Effect/Weapon.Seff"
PREVIEW_PARTICLES_PER_LAYER = 8


def weapon_family(item_type):
    family = WEAPON_TYPES.get(item_type, 0)
    return family if family != 19 else 0


def enchant_record(item_type, level):
    family = weapon_family(item_type)
    if family and 5 <= level < 50:
        return 6 * (family - 1) + min(level, 10) - 4
    return None


@dataclass(frozen=True, slots=True)
class EnchantEmitter:
    rate: int
    speed: float
    blend: int
    shape: int
    lifetime_ms: int
    alpha: float
    texture: str
    color: tuple[int, int, int]
    origin: tuple[float, float, float]
    extent: float
    half_size: float
    local: bool
    orbit_speed: float
    size_randomness: float
    vertical_speed: float

    def supported(self):
        return (self.shape in (0, 1, 2, 3, 6, 7, 8) and self.blend == 0 and self.local
                and 0 < self.rate <= 1000 and self.lifetime_ms > 0 and self.half_size > 0)


def read_seff(raw):
    if not 20 <= len(raw) <= 16 * 1024 * 1024:
        raise ModelFormatError("Invalid SEFF length.")
    reader = Cursor(raw)
    version = reader.unpack(I32)[0]
    if version not in (5, 6, 7):
        raise ModelFormatError(f"Unsupported SEFF version {version}.")
    reader.take(12)
    total = 0

    def count(stride):
        nonlocal total
        size = reader.count(stride)
        total += size
        if size > 16_384 or total > 262_144:
            raise ModelFormatError("SEFF table exceeds GX's count limits.")
        return size

    output = {}
    for _ in range(count(8)):
        record = reader.unpack(I32)[0]
        if record < 0 or record in output:
            raise ModelFormatError(f"Invalid or repeated SEFF record {record}.")
        layers = []
        for _ in range(count(64 if version == 5 else 68)):
            fields = reader.unpack(SEFF_FIELDS)
            length = count(2)
            if length > 256:
                raise ModelFormatError("SEFF texture name exceeds 256 UTF-16 units.")
            texture = reader.take(length * 2).decode("utf-16-le", errors="replace").rstrip("\x00")
            values = reader.unpack(SEFF_PLACEMENT)
            if version > 5:
                reader.take(4)
            if not all(isfinite(value) for value in (*fields, *values)):
                raise ModelFormatError(f"Invalid values in SEFF record {record}.")
            red, green, blue, x, y, z, extent, half_size, local, orbit, randomness, vertical = values
            layers.append(EnchantEmitter(*fields, texture, (red, green, blue), (x, y, z),
                                         extent, half_size, bool(local), orbit, randomness, vertical))
        output[record] = tuple(layers)
    reader.finish()
    return output


def normalized(vector):
    return vector / max(1e-8, np.linalg.norm(vector))


@dataclass(frozen=True, slots=True)
class EnchantLayer:
    emitter: EnchantEmitter
    texture: np.ndarray
    seed: int

    def particles(self, elapsed, axes, frame):
        e = self.emitter
        interval = (1000 // e.rate) / 1000
        hold = e.lifetime_ms / 1000
        initial = min(1, max(0, e.alpha))
        peak = min(1, initial + hold * 3)
        life = hold + peak / 2
        stride = max(1, ceil(life / interval / PREVIEW_PARTICLES_PER_LAYER))
        latest = int(elapsed / interval)
        first = max(1, latest - ceil(life / interval))
        tint = np.asarray(e.color, dtype=np.float32) / 255
        for birth in range(latest - latest % stride, first - 1, -stride):
            age = elapsed - birth * interval
            alpha = min(1, initial + age * 3) if age <= hold else peak - (age - hold) * 2
            if alpha <= 0:
                continue
            seeds = PARTICLE_RANDOM[(birth + self.seed) % len(PARTICLE_RANDOM)]
            direction = normalized(seeds[:3] * 2 - 1)
            extent = abs(e.extent)
            position = np.zeros(3, dtype=np.float32)
            if e.shape == 1:
                position = direction * (seeds[3] * extent * .2)
            elif e.shape in (2, 3):
                position = (seeds[3:6] - .5) * extent * .02
                position[e.shape - 2] *= 10
                direction = normalized(position)
            elif e.shape in (6, 7, 8):
                angle = (birth * interval + min(age, interval)) * e.orbit_speed
                s, c = sin(angle) * extent * .02, cos(angle) * extent * .02
                position[:] = ((0, c, s), (c, 0, -s), (c, s, 0))[e.shape - 6]
            position += np.asarray(e.origin) + direction * (seeds[6] * e.speed * age)
            position[1] += e.vertical_speed * .1 * age
            size = 2 * e.half_size * (1 + (2 * seeds[7] - 1) * abs(e.size_randomness))
            if not 0 < size or not np.isfinite(position).all():
                continue
            material = EffectMaterial(np.array((*tint, alpha), dtype=np.float32), 4, 1, True, camera_facing=True)
            particle = project_particle(QUAD, QUAD_UV, QUAD_FACES, min(size, frame.distance * 8),
                                        axes.T, position, axes, frame, material, self.texture)
            if particle is not None:
                yield particle


PARTICLE_RANDOM = np.random.default_rng(1).random((127, 8)).astype(np.float32)


class WeaponPreviewRenderer(EffectPreviewRenderer):
    @lru_cache(maxsize=1)
    def enchantments(self):
        return read_seff(self.effects.assets.read(SEFF_FILE))

    @lru_cache(maxsize=24)
    def weapon_effects(self, item_type, selector, level):
        family = weapon_family(item_type)
        problems = []
        effects = []
        if family and 0 <= selector < len(GLOW_FILES):
            path = f"Effect/{GLOW_FILES[selector]}"
            try:
                effects.append(self.placement(self.library(path), family - 1, IDENTITY, 0))
            except (OSError, ValueError) as error:
                problems.append(f"Glow: {error}")
        record = enchant_record(item_type, level)
        if record is not None:
            try:
                layers = self.enchantments().get(record)
                if not layers:
                    raise ModelFormatError("No matching recipe in Weapon.Seff.")
                if len(layers) > MAX_EMITTERS:
                    problems.append(f"SEFF #{record}: preview limited to {MAX_EMITTERS} emitters.")
                for i, emitter in enumerate(layers[:MAX_EMITTERS]):
                    try:
                        if not emitter.supported():
                            raise ModelFormatError(f"Unsupported layer {i + 1}.")
                        path = f"Effect/DDS/{basename(emitter.texture)}"
                        if self.effects.assets.resolve(path) is None:
                            raise FileNotFoundError(f"Missing {path}")
                        effects.append(EnchantLayer(emitter, self.effects.texture(path), i * 977 + record))
                    except (OSError, ValueError) as error:
                        problems.append(f"SEFF #{record}: {error}")
            except (OSError, ValueError) as error:
                problems.append(f"SEFF #{record}: {error}")
        return tuple(effects), tuple(problems)

    def render_effects(self, choice, kind, width, height, turn, item_type, level, show, elapsed):
        effects, problems = self.weapon_effects(item_type, choice.glow, level) if show else ((), ())
        canvas, depth, frame, axes = self.base_image(choice, kind, width, height, turn, bool(effects))
        if not effects:
            return canvas.tobytes(), problems, False
        streams = [effect.particles(elapsed, axes, frame) for effect in effects]
        return self.compose(canvas, depth, streams), problems, True
