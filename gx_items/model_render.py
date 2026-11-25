"""Render the local GX meshes with the equipment material and UV rules."""

from dataclasses import dataclass
from functools import lru_cache
import math
from struct import Struct

import numpy as np
from PIL import Image

from .equipment_models import Cursor, I32, ModelAssets, ModelChoice, ModelFormatError, Surface

ANI_HEADER = Struct("<IIH")
ROTATION_KEY = Struct("<I4f")
POSITION_KEY = Struct("<I3f")
STATIC_VERTEX = np.dtype([("position", "<f4", 3), ("normal", "<f4", 3), ("uv", "<f4", 2)])
SKIN_VERTEX = np.dtype([
    ("position", "<f4", 3), ("weight", "<f4"), ("bones", "u1", 4),
    ("normal", "<f4", 3), ("uv", "<f4", 2),
])
BACKGROUND = (238, 236, 231)
LIGHT = np.full(3, 1 / math.sqrt(3), dtype=np.float32)


@dataclass(frozen=True, slots=True)
class Mesh:
    vertices: np.ndarray
    faces: np.ndarray
    inverse_bind: np.ndarray | None = None


def read_mesh(data: bytes, kind: str) -> Mesh:
    reader = Cursor(data)
    inverse = None
    layout = STATIC_VERTEX if kind == "rigid" else SKIN_VERTEX
    if kind == "rigid":
        reader.name()
    elif kind == "cloth":
        if reader.unpack(I32)[0] != 0:
            raise ModelFormatError("Expected the GX cloth mesh layout")
    else:
        if reader.unpack(I32)[0] == 444:
            raise ModelFormatError("EP6 skin mesh is unsupported")
        bones = reader.count(64)
        inverse = np.frombuffer(reader.take(bones * 64), dtype="<f4").reshape(-1, 4, 4)
    vertex_count = reader.count(layout.itemsize)
    vertices = np.frombuffer(reader.take(vertex_count * layout.itemsize), dtype=layout)
    count = reader.count(6)
    faces = np.frombuffer(reader.take(count * 6), dtype="<u2").reshape(-1, 3)
    faces = faces[(faces < vertex_count).all(axis=1)]
    if not len(vertices) or not len(faces):
        raise ModelFormatError("The model has no valid triangles")
    return Mesh(vertices, faces, inverse)


def rotation_matrix(quaternion) -> np.ndarray:
    x, y, z, w = quaternion
    return np.array((
        (1 - 2 * (y*y + z*z), 2 * (x*y + z*w), 2 * (x*z - y*w)),
        (2 * (x*y - z*w), 1 - 2 * (x*x + z*z), 2 * (y*z + x*w)),
        (2 * (x*z + y*w), 2 * (y*z - x*w), 1 - 2 * (x*x + y*y)),
    ), dtype=np.float32)


def sample(keys: list[tuple], frame: int, quaternion: bool) -> np.ndarray | None:
    if not keys:
        return None
    if frame <= keys[0][0]:
        return np.array(keys[0][1:], dtype=np.float32)
    for before, after in zip(keys, keys[1:]):
        if frame <= after[0]:
            left, right = (np.array(key[1:], dtype=np.float32) for key in (before, after))
            blend = (frame - before[0]) / max(1, after[0] - before[0])
            if quaternion:
                dot = float(left @ right)
                if dot < 0:
                    dot, right = -dot, -right
                if dot < 0.9995:
                    angle = math.acos(max(0, dot))
                    return (math.sin((1 - blend) * angle) * left + math.sin(blend * angle) * right) / math.sin(angle)
                mixed = left + (right - left) * blend
                return mixed / max(1e-8, float(np.linalg.norm(mixed)))
            return left + (right - left) * blend
    return np.array(keys[-1][1:], dtype=np.float32)


@dataclass(frozen=True, slots=True)
class AnimationClip:
    start: int
    end: int
    bones: tuple

    def pose(self, frame: float) -> np.ndarray:
        result = []
        for parent, rest, rotations, positions in self.bones:
            local = rest.copy()
            rotation = sample(rotations, frame, True)
            position = sample(positions, frame, False)
            if rotation is not None:
                local[:3, :3] = rotation_matrix(rotation)
            if position is not None:
                local[3, :3] = position
            result.append(local @ result[parent] if 0 <= parent < len(result) else local)
        return np.asarray(result, dtype=np.float32).reshape(-1, 4, 4)


def read_animation(data: bytes) -> AnimationClip:
    reader = Cursor(data)
    start, end, count = reader.unpack(ANI_HEADER)
    if count > 512:
        raise ModelFormatError("Invalid ANI bone count")
    bones = []
    for _ in range(count):
        parent = reader.unpack(I32)[0]
        rest = np.frombuffer(reader.take(64), dtype="<f4").reshape(4, 4)
        rotations = [reader.unpack(ROTATION_KEY) for _ in range(reader.count(ROTATION_KEY.size))]
        positions = [reader.unpack(POSITION_KEY) for _ in range(reader.count(POSITION_KEY.size))]
        bones.append((parent, rest, rotations, positions))
    inverse = []
    for _, rest, _, _ in bones:
        try:
            with np.errstate(over="ignore", invalid="ignore"):
                matrix = np.linalg.inv(rest)
        except np.linalg.LinAlgError:
            matrix = None
        inverse.append(matrix if matrix is not None and np.isfinite(matrix).all() else np.eye(4, dtype=np.float32))
    local_bones = []
    for parent, rest, rotations, positions in bones:
        if 0 <= parent < count:
            with np.errstate(over="ignore", invalid="ignore"):
                local = rest @ inverse[parent]
            if np.isfinite(local).all():
                rest = local
        local_bones.append((parent, rest, rotations, positions))
    return AnimationClip(start, max(start, end), tuple(local_bones))


def idle_pose(data: bytes) -> np.ndarray:
    clip = read_animation(data)
    return clip.pose(clip.start)


def skin_mesh(mesh: Mesh, finals: np.ndarray | None):
    positions = mesh.vertices["position"]
    normals = mesh.vertices["normal"]
    if mesh.inverse_bind is None or finals is None:
        return positions, normals
    count = min(len(mesh.inverse_bind), len(finals))
    if not count:
        return positions, normals
    palette = mesh.inverse_bind[:count] @ finals[:count]
    bones = np.minimum(mesh.vertices["bones"][:, :2].astype(np.int32), count - 1)
    weight = np.clip(mesh.vertices["weight"], 0, 1)[:, None]
    first, second = palette[bones[:, 0]], palette[bones[:, 1]]
    transform = first * weight[:, :, None] + second * (1 - weight[:, :, None])
    return (
        np.einsum("ni,nij->nj", positions, transform[:, :3, :3]) + transform[:, 3, :3],
        np.einsum("ni,nij->nj", normals, transform[:, :3, :3]),
    )


def pose_cloth(positions: np.ndarray, surface: Surface, bones: np.ndarray | None) -> np.ndarray:
    if surface.cloth_grid is None:
        return positions
    columns, rows = surface.cloth_grid
    if len(positions) != columns * rows:
        raise ModelFormatError(f"Cloth mesh does not match its PC grid: {surface.mesh}")
    if bones is None:
        return positions

    vertices, adjustments = [], []
    for vertex, bone, offset in surface.pins:
        if not 0 <= vertex < len(positions):
            raise ModelFormatError(f"Invalid cloth pin: {surface.mesh}")
        if bone < len(bones):
            matrix = bones[bone]
            target = np.asarray(offset) @ matrix[:3, :3] + matrix[3, :3]
            vertices.append(vertex)
            adjustments.append(target - positions[vertex])
    if not vertices:
        return positions

    # Carry the collar displacement into the cloth while keeping the hem relaxed.
    offsets = np.empty_like(positions)
    offsets[:] = np.mean(adjustments, axis=0)
    offsets[vertices] = adjustments
    movable = np.ones(len(positions), dtype=bool)
    movable[vertices] = False
    movable = movable.reshape(rows, columns)
    grid = offsets.reshape(rows, columns, 3)
    for _ in range(2 * min(rows, 32)):
        padded = np.pad(grid, ((1, 1), (1, 1), (0, 0)), mode="edge")
        neighbors = padded[:-2, 1:-1] + padded[2:, 1:-1] + padded[1:-1, :-2] + padded[1:-1, 2:]
        grid[movable] = neighbors[movable] * .25
    return positions + offsets


@dataclass(frozen=True, slots=True)
class PreparedSurface:
    spec: Surface
    positions: np.ndarray
    normals: np.ndarray
    uv: np.ndarray
    faces: np.ndarray
    texture: np.ndarray
    uv2: np.ndarray | None = None
    lightmap: np.ndarray | None = None


@dataclass(frozen=True, slots=True)
class PreviewFrame:
    center: np.ndarray
    extent: np.ndarray
    distance: float
    focal: float


class PreviewRenderer:
    def __init__(self, assets: ModelAssets):
        self.assets = assets

    @lru_cache(maxsize=32)
    def texture(self, name: str) -> np.ndarray:
        path = self.assets.resolve(name)
        if path is None:
            return np.full((1, 1, 4), 255, dtype=np.uint8)
        try:
            with Image.open(path) as source:
                if max(source.size) > 4096:
                    raise ModelFormatError(f"Texture is larger than 4096 pixels: {name}")
                return np.asarray(source.convert("RGBA"))
        except Image.DecompressionBombError as error:
            raise ModelFormatError(f"Texture is larger than 4096 pixels: {name}") from error

    @lru_cache(maxsize=16)
    def pose(self, name: str) -> np.ndarray | None:
        if not name or self.assets.resolve(name) is None:
            return None
        return idle_pose(self.assets.read(name))

    @lru_cache(maxsize=32)
    def prepare(self, surface: Surface) -> PreparedSurface:
        mesh = read_mesh(self.assets.read(surface.mesh), surface.mesh_kind)
        bones = self.pose(surface.pose)
        positions, normals = skin_mesh(mesh, bones)
        positions = pose_cloth(positions, surface, bones)
        uv = mesh.vertices["uv"]
        if not (np.isfinite(positions).all() and np.isfinite(uv).all()):
            raise ModelFormatError(f"Invalid geometry: {surface.mesh}")
        invalid_normals = ~np.isfinite(normals).all(axis=1)
        if invalid_normals.any():
            normals = normals.copy()
            normals[invalid_normals] = (0, 1, 0)
        return PreparedSurface(surface, positions, normals, uv, mesh.faces, self.texture(surface.texture))

    @lru_cache(maxsize=32)
    def frame(self, choice: ModelChoice, kind: str, width: int, height: int) -> PreviewFrame:
        positions = np.concatenate([self.prepare(spec).positions for spec in choice.surfaces])
        return fit_frame(positions, kind, width, height)

    def render(self, choice: ModelChoice, kind: str, width: int, height: int, turn: float = 0) -> Image.Image:
        surfaces = tuple(self.prepare(spec) for spec in choice.surfaces)
        frame = self.frame(choice, kind, width, height)
        return render_surfaces(surfaces, frame, kind, width, height, turn)


def sample_texture(texture: np.ndarray, u: np.ndarray, v: np.ndarray, clamp=False) -> np.ndarray:
    height, width = texture.shape[:2]
    x, y = u * width - .5, v * height - .5
    i, j = np.floor(x), np.floor(y)
    a, b = (x - i)[:, None], (y - j)[:, None]
    i, j = i.astype(np.int32), j.astype(np.int32)
    if clamp:
        left, right = np.clip(i, 0, width - 1), np.clip(i + 1, 0, width - 1)
        top, bottom = np.clip(j, 0, height - 1), np.clip(j + 1, 0, height - 1)
    else:
        left, right = i % width, (i + 1) % width
        top, bottom = j % height, (j + 1) % height
    return (
        ((1 - a) * texture[top, left] + a * texture[top, right]) * (1 - b)
        + ((1 - a) * texture[bottom, left] + a * texture[bottom, right]) * b
    )


def camera_axes(kind: str, turn: float, extent: np.ndarray) -> np.ndarray:
    yaw = {"weapon": 23, "shield": 35, "armor": 192, "cape": 192, "mount": 120, "npc": 180}.get(kind, 30)
    if kind == "weapon" and extent[2] > extent[0] * 1.1:
        yaw = 75
    yaw = math.radians(yaw + turn)
    pitch = math.radians({"shield": -58, "weapon": 13, "dungeon": 49, "water": 53}.get(kind, 9))
    outward = np.array((math.sin(yaw)*math.cos(pitch), math.sin(pitch), math.cos(yaw)*math.cos(pitch)))
    right = np.array((math.cos(yaw), 0, -math.sin(yaw)))
    up = np.cross(outward, right)
    if kind in ("mount", "npc"):
        # MON geometry uses the native left-handed view and clockwise faces.
        right = -right
    if kind == "weapon":
        tilt = math.radians(14)
        right, up = right * math.cos(tilt) + up * math.sin(tilt), up * math.cos(tilt) - right * math.sin(tilt)
    return np.column_stack((right, up, outward)).astype(np.float32)


def fit_frame(positions: np.ndarray, kind: str, width: int, height: int) -> PreviewFrame:
    extent = np.ptp(positions, axis=0)
    axes = camera_axes(kind, 0, extent)
    reference = positions @ axes
    center = (reference.min(axis=0) + reference.max(axis=0)) * .5 @ axes.T
    focal = height / (2 * math.tan(math.radians(17)))
    size = np.array((width, height), dtype=np.float32)
    orbit = positions - center
    distance = float(np.linalg.norm(orbit, axis=1).max()) + .01
    for turn in range(0, 360, 20):
        offset = orbit @ camera_axes(kind, turn, extent)
        # Keep the framing of the reference angle and leave enough space at
        # other angles to rotate without changing scale or cropping the mesh.
        margin = .41 if kind == "weapon" else .43
        half_view = size * (margin if turn == 0 else .48)
        footprint = (np.abs(offset[:, :2]) / half_view).max(axis=1)
        distance = max(distance, float((offset[:, 2] + focal * footprint).max()))
    return PreviewFrame(center, extent, distance, focal)


def render_surfaces(
    surfaces: tuple[PreparedSurface, ...], frame: PreviewFrame, kind: str, width: int, height: int, turn: float,
) -> Image.Image:
    canvas, _ = model_buffers(surfaces, frame, kind, width, height, camera_axes(kind, turn, frame.extent))
    return Image.fromarray(canvas)


def model_buffers(surfaces, frame, kind, width, height, axes, *, background=BACKGROUND):
    canvas = np.empty((height, width, 3), dtype=np.uint8)
    canvas[:] = background
    depth_buffer = np.zeros((height, width), dtype=np.float32)
    for surface in surfaces:
        points = (surface.positions - frame.center) @ axes
        depth = frame.distance - points[:, 2]
        screen = points[:, :2] * (frame.focal / depth[:, None])
        screen[:, 0] += width * .5
        screen[:, 1] = height * .5 - screen[:, 1]
        if kind in ("weapon", "shield", "world", "dungeon"):
            norms = surface.normals / np.maximum(1e-7, np.linalg.norm(surface.normals, axis=1)[:, None])
            diffuse = np.minimum(1, 128 / 255 + np.maximum(0, norms @ LIGHT))
        else:
            diffuse = np.ones(len(points), dtype=np.float32)
        draw_triangles(canvas, depth_buffer, screen, 1 / depth, diffuse, surface)
    return canvas, depth_buffer


def draw_triangles(canvas, zbuffer, projected, reciprocal_depth, diffuse, surface: PreparedSurface, *, material=None):
    height, width = zbuffer.shape
    faces = surface.faces
    bounds = projected[faces]
    low = np.maximum(0, np.floor(bounds.min(axis=1))).astype(np.int32)
    high = np.minimum((width, height), np.ceil(bounds.max(axis=1))).astype(np.int32)
    spans = high - low
    a, b, c = bounds.transpose(1, 0, 2)
    ab, ac = b - a, c - a
    area = ab[:, 0] * ac[:, 1] - ab[:, 1] * ac[:, 0]
    winding = np.abs(area) if surface.spec.two_sided else area
    visible = (winding >= 1e-6) & (spans > 0).all(axis=1)
    faces, a, ab, ac, area, low, spans = (
        values[visible] for values in (faces, a, ab, ac, area, low, spans)
    )
    counts = spans[:, 0] * spans[:, 1]
    offsets = np.r_[0, np.cumsum(counts)]
    flat_depth, flat_pixels = zbuffer.ravel(), canvas.reshape(-1, 3)
    start = 0
    while start < len(faces):
        # Bound temporary arrays even when a face covers most of the viewport.
        stop = max(start + 1, int(np.searchsorted(offsets, offsets[start] + 131_072, side="right")) - 1)
        region = slice(start, stop)
        ids = np.repeat(np.arange(start, stop), counts[region])
        positions = np.arange(offsets[start], offsets[stop]) - offsets[ids]
        x = low[ids, 0] + positions % spans[ids, 0]
        y = low[ids, 1] + positions // spans[ids, 0]
        px = y * width + x
        xx = x.astype(np.float32) + .5 - a[ids, 0]
        yy = y.astype(np.float32) + .5 - a[ids, 1]
        beta = (xx * ac[ids, 1] - yy * ac[ids, 0]) / area[ids]
        gamma = (ab[ids, 0] * yy - ab[ids, 1] * xx) / area[ids]
        alpha = 1 - beta - gamma
        start = stop
        inside = (alpha >= -1e-6) & (beta >= -1e-6) & (gamma >= -1e-6)
        if not inside.any():
            continue
        triangles, pixels = faces[ids[inside]], px[inside]
        weights = np.column_stack((alpha[inside], beta[inside], gamma[inside]))
        weights *= reciprocal_depth[triangles]
        depth = weights[:, 0] + weights[:, 1] + weights[:, 2]
        closer = depth > flat_depth[pixels]
        triangles, pixels, weights, depth = triangles[closer], pixels[closer], weights[closer], depth[closer]
        weights /= depth[:, None]
        texels = None
        if material is not None or surface.spec.cutout:
            uv = np.einsum("ij,ijk->ik", weights, surface.uv[triangles])
            texels = sample_texture(surface.texture, uv[:, 0], uv[:, 1], clamp=material is not None and material.clamp)
            keep = (texels[:, 3] > 127.5 if material is None else material.visible(texels))
            texels = texels[keep]
            triangles, pixels, weights, depth = triangles[keep], pixels[keep], weights[keep], depth[keep]
        if not len(pixels):
            continue
        order = np.lexsort((-depth, pixels))
        sorted_pixels = pixels[order]
        nearest = order[np.r_[True, sorted_pixels[1:] != sorted_pixels[:-1]]]
        triangles, pixels, weights = triangles[nearest], pixels[nearest], weights[nearest]
        if texels is None:
            uv = np.einsum("ij,ijk->ik", weights, surface.uv[triangles])
            texels = sample_texture(surface.texture, uv[:, 0], uv[:, 1])
        else:
            texels = texels[nearest]
        if material is not None:
            flat_pixels[pixels] = material.composite(texels, flat_pixels[pixels])
            continue
        lighting = np.einsum("ij,ij->i", weights, diffuse[triangles])
        colors = texels[:, :3] * lighting[:, None]
        if surface.lightmap is not None:
            uv2 = np.einsum("ij,ijk->ik", weights, surface.uv2[triangles])
            colors *= sample_texture(surface.lightmap, uv2[:, 0], uv2[:, 1], clamp=True)[:, :3] / 255
        flat_pixels[pixels] = np.clip(colors, 0, 255).astype(np.uint8)
        flat_depth[pixels] = depth[nearest]
