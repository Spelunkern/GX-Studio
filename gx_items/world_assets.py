"""Standalone geometry and animation formats from the GX world renderer."""

from dataclasses import dataclass
from math import isfinite
from struct import Struct

import numpy as np

from .catalog import decode_text
from .equipment_models import Cursor, I32, ModelFormatError

WORLD_VERTEX = np.dtype([
    ("position", "<f4", 3), ("normal", "<f4", 3), ("bone", "<i4"), ("uv", "<f4", 2),
])
DUNGEON_VERTEX = np.dtype([*WORLD_VERTEX.descr, ("uv2", "<f4", 2)])
WATER_HEADER = Struct("<fIi")
ROTATION = Struct("<i4f")


@dataclass(frozen=True, slots=True)
class WorldPart:
    texture: str
    vertices: np.ndarray
    faces: np.ndarray
    lightmap: int = -1


def faces(reader):
    return np.frombuffer(reader.take(reader.count(6) * 6), dtype="<u2").reshape(-1, 3)


def vertices(reader, dtype=WORLD_VERTEX, frames=1):
    count = reader.count(dtype.itemsize * frames)
    return np.frombuffer(reader.take(count * frames * dtype.itemsize), dtype=dtype).reshape(frames, count)


def read_smod(raw):
    reader = Cursor(raw)
    reader.take(40)
    parts = []
    for _ in range(reader.count(12)):
        texture = reader.name()
        points = vertices(reader)
        parts.append(WorldPart(texture, points, faces(reader)))
    return tuple(parts)


def read_vani(raw):
    reader = Cursor(raw)
    reader.take(40)
    count, frame_count, interval = reader.unpack(Struct("<3i"))
    if not 0 <= count <= min(65536, (len(raw) - reader.offset) // 12) or not 1 <= frame_count <= 65536:
        raise ModelFormatError("Invalid VANI mesh or frame count")
    parts = []
    for _ in range(count):
        texture = reader.name()
        triangles = faces(reader)
        parts.append(WorldPart(texture, vertices(reader, frames=frame_count), triangles))
    reader.take(28)
    return tuple(parts), interval if interval > 0 else 1000 / 30


def read_dg(raw):
    reader = Cursor(raw)
    reader.take(24)
    textures = tuple(decode_text(reader.take(256)) for _ in range(reader.count(256)))
    lightmaps = reader.unpack(I32)[0]
    parts = []

    def node(depth):
        if depth > 32:
            raise ModelFormatError("DG octree is too deep")
        reader.take(60)
        for _ in range(reader.count(8)):
            texture_index = reader.unpack(I32)[0]
            texture = textures[texture_index] if 0 <= texture_index < len(textures) else ""
            for _ in range(reader.count(12)):
                lightmap = reader.unpack(I32)[0]
                points = vertices(reader, DUNGEON_VERTEX)
                parts.append(WorldPart(texture, points, faces(reader), lightmap if 0 <= lightmap < lightmaps else -1))
        if reader.unpack(I32)[0] == 1:
            reader.take(reader.count(12) * 12)
            reader.take(reader.count(6) * 6)
        for _ in range(8):
            if reader.unpack(I32)[0] > 0:
                node(depth + 1)

    if reader.unpack(I32)[0] > 0:
        node(0)
    return tuple(parts)


def read_wtr(raw):
    reader = Cursor(raw)
    scale, color, interval = reader.unpack(WATER_HEADER)
    if not isfinite(scale) or scale <= 0 or interval < 0:
        raise ModelFormatError("Invalid water tile size or interval")
    count = reader.count(256)
    if count > 4096:
        raise ModelFormatError("Too many water frames")
    names = tuple(decode_text(reader.take(256)) for _ in range(count))
    reader.finish()
    return scale, color, interval, names


def read_mani(raw):
    reader = Cursor(raw)
    if reader.unpack(I32)[0] != 33:
        raise ModelFormatError("Unknown MANI version")
    reader.take(56)
    enabled, x, y, z, rate = reader.unpack(ROTATION)
    reader.take(28)
    if not all(map(isfinite, (x, y, z, rate))):
        raise ModelFormatError("Invalid MANI rotation")
    return (x, y, z), rate * 30 if enabled else 0.0
