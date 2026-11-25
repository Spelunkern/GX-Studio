"""Preview the client's EFT emitters over the MON model's idle pose."""

from dataclasses import dataclass
from functools import lru_cache
from math import ceil, cos, floor, isfinite, radians, sin
from struct import Struct

import numpy as np

from .equipment_models import Cursor, I32, ModelAssets, ModelFormatError, Surface, basename
from .model_render import (
    BACKGROUND, PreparedSurface, PreviewRenderer, camera_axes, draw_triangles, model_buffers, read_mesh, sample_texture,
)

FLAGS = Struct("<10i")
TIMES = Struct("<8f")
VECTORS = Struct("<15f")
PHYSICS = Struct("<3i3f")
ROTATION = Struct("<fiifi")
MATERIAL = Struct("<4i")
SEQUENCE_ENTRY = Struct("<if")
VERTEX = np.dtype([("position", "<f4", 3), ("bone", "<i4"), ("uv", "<f4", 2)])
QUAD = np.array(((-.5, -.5, 0), (.5, -.5, 0), (.5, .5, 0), (-.5, .5, 0)), dtype=np.float32)
QUAD_UV = np.array(((0, 1), (1, 1), (1, 0), (0, 0)), dtype=np.float32)
QUAD_FACES = np.array(((0, 1, 2), (0, 2, 3)))
MIRROR_QUAD = np.array([(x * .5 - .5, y * .5 - .5, 0) for y in range(3) for x in range(3)], dtype=np.float32)
MIRROR_UV = np.array([(1 - x % 2, y % 2) for y in range(3) for x in range(3)], dtype=np.float32)
MIRROR_FACES = np.array([face for i in (0, 1, 3, 4) for face in ((i, i + 1, i + 3), (i + 3, i + 1, i + 4))])
EFFECT_SURFACE = Surface("", "", "rigid", two_sided=True)
IDENTITY = np.eye(4, dtype=np.float32)
# World-space EFT placement applies GX's basis conversion to the model origin.
ACTION_BASIS = np.diag(np.array((-1, 1, -1, 1), dtype=np.float32))
MAX_PARTICLES = 32
MAX_EMITTERS = 32


def read_keys(reader, columns):
    layout = Struct(f"<{columns}f")
    keys = [reader.unpack(layout) for _ in range(reader.count(layout.size))]
    return np.array(sorted(keys, key=lambda key: key[-1]), dtype=np.float32).reshape(-1, columns)


@dataclass(frozen=True, slots=True)
class Emitter:
    name: str
    loop: bool
    source: int
    destination: int
    orbit: int
    loop_frames: bool
    mesh_index: int
    billboard: bool
    frame_delay: float
    rate: float
    single_birth: bool
    life: float
    duration: float
    orbit_speed: float
    vectors: np.ndarray
    base_axis: int
    drift: bool
    attraction: np.ndarray | None
    attraction_strength: float
    spin: float
    random_spin: bool
    spin_axis: int
    colors: np.ndarray
    velocity_curve: np.ndarray
    sizes: np.ndarray
    mirror: bool
    tilt_axis: int
    tilt_min: int
    tilt_max: int
    textures: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class EffectLibrary:
    meshes: tuple[str, ...]
    textures: tuple[str, ...]
    emitters: tuple[Emitter, ...]
    sequences: tuple[tuple[tuple[int, float], ...], ...]
    sequence_names: tuple[str, ...] = ()


def read_eft(data):
    reader = Cursor(data)
    if len(data) > 16 * 1024 * 1024 or reader.take(3) != b"EFT":
        raise ModelFormatError("Expected a GX EFT file")
    meshes, textures = reader.names(), reader.names()
    emitters = []
    for _ in range(reader.count(212)):
        name = reader.name()
        flags, times = reader.unpack(FLAGS), reader.unpack(TIMES)
        vectors = np.array(reader.unpack(VECTORS), dtype=np.float32).reshape(5, 3)
        base, drift, attract, *attractor = reader.unpack(PHYSICS)
        strength, random_spin, spin_enabled, spin, spin_axis = reader.unpack(ROTATION)
        colors, velocity, sizes = (read_keys(reader, count) for count in (5, 2, 3))
        mirror, tilt_axis, tilt_min, tilt_max = reader.unpack(MATERIAL)
        frames = tuple(reader.unpack(I32)[0] for _ in range(reader.count(4)))
        if (not all(map(isfinite, (*times, *attractor, strength, spin)))
                or not all(np.isfinite(values).all() for values in (vectors, colors, velocity, sizes))):
            raise ModelFormatError(f"Invalid emitter values: {name}")
        emitters.append(Emitter(
            name, bool(flags[3]), flags[6], flags[4], flags[5], bool(flags[7]), flags[8], bool(flags[9]),
            times[0], max(0, (times[1] + times[3]) * .5), times[1] == times[3] == 1,
            max(0, (times[2] + times[4]) * .5),
            max(0, times[5]), times[6], vectors, base, bool(drift),
            np.asarray(attractor, dtype=np.float32) if attract else None, strength,
            spin if spin_enabled else 0, bool(random_spin), spin_axis, colors, velocity, sizes,
            bool(mirror), tilt_axis, tilt_min, tilt_max, frames,
        ))
    sequences, sequence_names = [], []
    for _ in range(reader.count(8)):
        sequence_names.append(reader.name())
        entries = tuple(reader.unpack(SEQUENCE_ENTRY) for _ in range(reader.count(SEQUENCE_ENTRY.size)))
        if not all(isfinite(delay) for _, delay in entries):
            raise ModelFormatError("Invalid EFT sequence delay")
        sequences.append(entries)
    reader.finish()
    return EffectLibrary(meshes, textures, tuple(emitters), tuple(sequences), tuple(sequence_names))


@dataclass(frozen=True, slots=True)
class EffectMesh:
    vertices: np.ndarray
    uv: np.ndarray
    faces: np.ndarray
    maximum: int
    keys: tuple[int, ...]
    frames: tuple[np.ndarray, ...]

    def sample(self, age, loop):
        if not self.keys:
            return self.vertices, self.uv
        clock = (age * 30) % (self.maximum + .5) if loop else min(age * 30, self.maximum)
        first = max(0, min(len(self.keys) - 1, int(np.searchsorted(self.keys, clock - 2, side="right"))))
        if clock < self.keys[first]:
            first = max(0, first - 1)
        snapshot = self.frames[first]
        if first + 1 < len(self.keys) and clock < self.keys[-1]:
            interval = self.keys[first + 1] - self.keys[first]
            if interval > 0:
                snapshot = snapshot + (self.frames[first + 1] - snapshot) * ((clock - self.keys[first]) / interval)
        return snapshot[:, :3], snapshot[:, 3:]


def read_effect_mesh(data):
    reader = Cursor(data)
    reader.name()
    count = reader.count(VERTEX.itemsize)
    vertices = np.frombuffer(reader.take(count * VERTEX.itemsize), dtype=VERTEX)
    faces = np.frombuffer(reader.take(reader.count(6) * 6), dtype="<u2").reshape(-1, 3)
    maximum = reader.unpack(I32)[0]
    frame_count = reader.count(4 + count * 20)
    if not 0 < count <= 65536 or maximum < 0 or frame_count > 4096 or count * frame_count > 1 << 20:
        raise ModelFormatError("Invalid 3DE geometry or animation size")
    if (faces >= count).any() or not np.isfinite(vertices["position"]).all() or not np.isfinite(vertices["uv"]).all():
        raise ModelFormatError("Invalid 3DE geometry")
    keys, frames = [], []
    for _ in range(frame_count):
        key = reader.unpack(I32)[0]
        frame = np.frombuffer(reader.take(count * 20), dtype="<f4").reshape(-1, 5)
        if key < 0 or (keys and key < keys[-1]) or not np.isfinite(frame).all():
            raise ModelFormatError("Invalid 3DE keyframe")
        keys.append(key)
        frames.append(frame)
    return EffectMesh(vertices["position"], vertices["uv"], faces, maximum, tuple(keys), tuple(frames))


def rotation(axis, angle):
    if axis not in (1, 2, 3) or not angle:
        return IDENTITY[:3, :3]
    matrix = np.eye(3, dtype=np.float32)
    a, b = axis % 3, (axis + 1) % 3
    matrix[a, a] = matrix[b, b] = cos(angle)
    matrix[a, b], matrix[b, a] = sin(angle), -sin(angle)
    return matrix


@dataclass(frozen=True, slots=True)
class EffectMaterial:
    rgba: np.ndarray
    source: int
    destination: int
    clamp: bool
    camera_facing: bool = False

    def visible(self, texels):
        return texels[:, 3] * self.rgba[3] > 0

    def composite(self, texels, pixels):
        source, destination = self.source, self.destination
        if (source, destination) == (4, 1):
            alpha = texels[:, 3:] * (self.rgba[3] / 255)
            return np.minimum(255, pixels + texels[:, :3] * self.rgba[:3] * alpha)
        if source in (11, 12):
            destination = 5 if source == 11 else 4
        rgb = texels[:, :3] * (self.rgba[:3] / 255)
        alpha = texels[:, 3:] * (self.rgba[3] / 255)
        target = pixels.astype(np.float32) / 255
        # The preview framebuffer is opaque, so DST_ALPHA is always one.
        factors = (0, 1, rgb, 1 - rgb, alpha, 1 - alpha, 1, 0, target, 1 - target, 0, alpha, 1 - alpha)
        return np.clip((rgb * factors[source] + target * factors[destination]) * 255, 0, 255)


@dataclass(frozen=True, slots=True)
class Placement:
    emitter: Emitter
    textures: tuple[np.ndarray, ...]
    mesh: EffectMesh | None
    path: EffectMesh | None
    matrix: np.ndarray
    delay: float

    def ages(self, elapsed):
        e = self.emitter
        elapsed -= self.delay
        if elapsed <= 0 or e.rate <= 0:
            return ()
        if e.single_birth or e.life == 0:
            first_birth = e.duration if e.single_birth else 1 / e.rate
            if elapsed < first_birth or (not e.loop and first_birth > e.duration):
                return ()
            age = elapsed - first_birth
            if not e.life:
                return ((0, age),)
            if e.loop:
                cycle = int(age / max(e.life, 1 / 60))
                age %= max(e.life, 1 / 60)
            else:
                cycle = 0
            return ((cycle, age),) if age < e.life else ()
        # Sample a bounded number of births at evenly spaced intervals. As the
        # view rotates or rerenders the same instant, the same births are used.
        interval = max(1 / e.rate, e.life / 8)
        latest_birth = elapsed if e.loop else min(elapsed, e.duration)
        return tuple(
            (index, elapsed - interval * index)
            for index in range(int(latest_birth / interval), max(0, ceil((elapsed - e.life) / interval) - 1), -1)
        )

    def particles(self, elapsed, axes, frame):
        e = self.emitter
        ages = self.ages(elapsed)
        if not ages:
            return
        clock = int((elapsed - self.delay) / max(e.frame_delay, 1 / 60))
        index = clock % len(self.textures) if e.loop_frames else min(clock, len(self.textures) - 1)
        texture = self.textures[index]
        basis = self.matrix[:3, :3]
        if e.base_axis == 0:
            facing = axes.T
        elif e.base_axis == 1:
            facing = rotation(1, radians(90))
        elif e.base_axis == 2:
            facing = rotation(2, float(np.arctan2(-axes[0, 2], -axes[2, 2])))
        else:
            facing = basis
        spread, gravity, origin, velocity_min, velocity_max = e.vectors
        for birth, age in ages:
            seeds = PARTICLE_SEEDS[birth % len(PARTICLE_SEEDS)]
            velocity = velocity_min + (velocity_max - velocity_min) * seeds[:3]
            position = origin + spread * (2 * seeds[3:6] - 1)
            if self.path is not None:
                index = max(0, int((elapsed - self.delay - age) * 30)) % len(self.path.vertices)
                position = position + self.path.vertices[index]
            if e.orbit:
                offset = velocity @ rotation((2, 3, 1)[(e.orbit - 1) % 3], age * e.orbit_speed)
            else:
                offset = velocity * age
            if len(e.velocity_curve):
                acceleration = np.interp(age, e.velocity_curve[:, -1], e.velocity_curve[:, 0])
                offset = offset * np.clip(1 + acceleration * age * .5, -8, 8)
            if e.drift:
                offset = offset + gravity * (.5 * age * age)
            if e.attraction is not None:
                direction = (e.attraction - position) @ basis - offset
                offset = offset + direction * (.5 * e.attraction_strength * age * age / max(1e-6, np.linalg.norm(direction)))
            center = position @ basis + self.matrix[3, :3] + offset
            colors = e.colors
            if len(colors):
                rgba = np.array([np.interp(age, colors[:, -1], colors[:, col], left=0) for col in range(4)])
            else:
                rgba = np.full(4, min(age / e.life, 1) if e.life else 1)
            if rgba[3] <= 0:
                continue
            size = .3
            if len(e.sizes):
                values = e.sizes[:, 0] + (e.sizes[:, 1] - e.sizes[:, 0]) * seeds[6]
                size = np.interp(age, e.sizes[:, -1], values)
            if size <= 0 or not np.isfinite(center).all():
                continue
            # Clamp extreme coordinates before forming screen-space bounding boxes.
            if np.linalg.norm(center - frame.center) > frame.distance * 16:
                continue
            size = min(size, frame.distance * 8)
            tilt = radians(e.tilt_min + (e.tilt_max - e.tilt_min) * seeds[7])
            spin = age * e.spin * (seeds[7] if e.random_spin else 1)
            orient = rotation(e.tilt_axis, tilt) @ rotation(e.spin_axis, spin) @ facing
            if self.mesh is not None:
                vertices, uv = self.mesh.sample(age, e.loop_frames)
                faces = self.mesh.faces
            elif e.mirror:
                vertices, uv, faces = MIRROR_QUAD, MIRROR_UV, MIRROR_FACES
            else:
                vertices, uv, faces = QUAD, QUAD_UV, QUAD_FACES
            material = EffectMaterial(np.clip(rgba, 0, 1), e.source, e.destination, e.mirror)
            particle = project_particle(vertices, uv, faces, size, orient, center, axes, frame, material, texture)
            if particle is not None:
                yield particle


def project_particle(vertices, uv, faces, size, orientation, center, axes, frame, material, texture):
    points = ((vertices * size) @ orientation + center - frame.center) @ axes
    depth = frame.distance - points[:, 2]
    if not np.isfinite(points).all():
        return None
    faces = faces[(depth[faces] > .01).all(axis=1)]
    if not len(faces):
        return None
    safe_depth = np.maximum(depth, .01)
    projected = np.clip(points[:, :2] * (frame.focal / safe_depth[:, None]), -1e6, 1e6)
    surface = PreparedSurface(EFFECT_SURFACE, vertices, None, uv, faces, texture)
    return (float((center - frame.center) @ axes[:, 2]), projected, 1 / safe_depth, material, surface)


def draw_billboard(canvas, depth, projected, reciprocal_depth, material, texture):
    # A camera-facing SEFF quad has one depth and maps its whole texture to a
    # screen rectangle. Sample that rectangle once, including its diagonal.
    left, bottom = projected[0]
    right, top = projected[2]
    if right <= left or bottom <= top:
        return
    height, width = depth.shape
    x0, x1 = max(0, ceil(left - .5)), min(width, floor(right - .5) + 1)
    y0, y1 = max(0, ceil(top - .5)), min(height, floor(bottom - .5) + 1)
    if x1 <= x0 or y1 <= y0:
        return
    y, x = np.nonzero(depth[y0:y1, x0:x1] < reciprocal_depth)
    x, y = x + x0, y + y0
    u = (x.astype(np.float32) + .5 - left) / (right - left)
    v = (y.astype(np.float32) + .5 - top) / (bottom - top)
    texels = sample_texture(texture, u, v, clamp=material.clamp)
    visible = material.visible(texels)
    x, y = x[visible], y[visible]
    canvas[y, x] = material.composite(texels[visible], canvas[y, x])


PARTICLE_SEEDS = np.random.default_rng(0).random((8, 8)).astype(np.float32)


class EffectPreviewRenderer(PreviewRenderer):
    def __init__(self, assets, effect_root):
        super().__init__(assets)
        self.effects = PreviewRenderer(ModelAssets(effect_root))

    @lru_cache(maxsize=16)
    def library(self, file):
        return read_eft(self.effects.assets.read(file))

    @lru_cache(maxsize=16)
    def effect_mesh(self, file):
        return read_effect_mesh(self.effects.assets.read(file))

    def placement(self, library, index, matrix, delay):
        if not 0 <= index < len(library.emitters):
            raise ModelFormatError(f"Emitter {index} is out of range.")
        emitter = library.emitters[index]
        if not (0 <= emitter.source <= 12 and 0 <= emitter.destination <= 12):
            raise ModelFormatError(f"Emitter {index} has an unsupported blend mode.")
        names = [f"Effect/DDS/{basename(library.textures[i])}" for i in emitter.textures if 0 <= i < len(library.textures)]
        if not names:
            raise ModelFormatError(f"Emitter {index} has no valid textures.")
        for name in names:
            if self.effects.assets.resolve(name) is None:
                raise FileNotFoundError(f"Missing {name}")
        mesh = path = None
        if 0 <= emitter.mesh_index < len(library.meshes):
            name = f"Effect/3DE/{basename(library.meshes[emitter.mesh_index])}"
            if emitter.billboard:
                if self.effects.assets.resolve(name) is not None:
                    path = self.effect_mesh(name)
            else:
                mesh = self.effect_mesh(name)
        elif emitter.mesh_index >= 0 and not emitter.billboard:
            raise ModelFormatError(f"Emitter {index} has an invalid 3DE mesh index.")
        return Placement(emitter, tuple(self.effects.texture(name) for name in names), mesh, path, matrix, delay)

    @lru_cache(maxsize=4)
    def base_image(self, choice, kind, width, height, turn, show_effects):
        frame = self.frame(choice, kind, width, height)
        axes = camera_axes(kind, turn, frame.extent)
        surfaces = tuple(self.prepare(spec) for spec in choice.surfaces)
        # Additive particles need contrast against the preview background.
        background = (108, 108, 110) if show_effects else BACKGROUND
        canvas, depth = model_buffers(surfaces, frame, kind, width, height, axes, background=background)
        return canvas, depth, frame, axes

    def compose(self, canvas, depth, streams, *, include_each=False):
        canvas = canvas.copy()
        height, width = depth.shape
        particles = []
        # A full effect gives every active emitter a turn before sampling extra particles.
        while streams and (include_each or len(particles) < MAX_PARTICLES):
            next_streams = []
            for stream in streams:
                particle = next(stream, None)
                if particle is not None:
                    particles.append(particle)
                    next_streams.append(stream)
                if not include_each and len(particles) >= MAX_PARTICLES:
                    break
            streams = next_streams
            include_each = False
        for _, projected, reciprocal_depth, material, surface in sorted(particles, key=lambda p: p[0]):
            projected[:, 0] += width * .5
            projected[:, 1] = height * .5 - projected[:, 1]
            if material.camera_facing:
                draw_billboard(canvas, depth, projected, reciprocal_depth[0], material, surface.texture)
            else:
                draw_triangles(canvas, depth, projected, reciprocal_depth, None, surface, material=material)
        return canvas.tobytes()


class MonPreviewRenderer(EffectPreviewRenderer):
    @lru_cache(maxsize=8)
    def skeleton(self, choice):
        spec = choice.surfaces[0]
        bones = self.pose(spec.pose)
        if bones is not None and len(bones):
            return bones
        inverse = read_mesh(self.assets.read(spec.mesh), spec.mesh_kind).inverse_bind
        return np.linalg.pinv(inverse) if inverse is not None and len(inverse) else ()

    @lru_cache(maxsize=8)
    def scene(self, choice, attachments, action):
        placements, problems = [], []

        def add(library, index, matrix, delay):
            if len(placements) == MAX_EMITTERS:
                problems.append(f"The preview shows up to {MAX_EMITTERS} emitters.")
                return
            try:
                placements.append(self.placement(library, index, matrix, delay))
            except (OSError, ValueError) as error:
                problems.append(str(error))

        active = [(bone, emitter) for bone, emitter in attachments if bone >= 0 and emitter >= 0]
        if active:
            try:
                bones = self.skeleton(choice)
                file = "Effect/Monster.EFT"
                if self.effects.assets.resolve(file) is None and self.effects.assets.resolve("Monster/Monster.EFT"):
                    file = "Monster/Monster.EFT"
                library = self.library(file)
                for bone, eid in active:
                    if bone < len(bones):
                        add(library, eid, bones[bone], 0)
                    else:
                        problems.append(f"Bone {bone} is out of range for this model.")
            except (OSError, ValueError, np.linalg.LinAlgError) as error:
                problems.append(f"Bone effects: {error}")
        action_placements = []
        if action:
            try:
                library = self.library(f"Effect/{basename(action)}")
                if not library.sequences:
                    raise ModelFormatError("No EFT sequences")
                count = len(placements)
                for index, delay in library.sequences[0]:
                    add(library, index, ACTION_BASIS, max(0, delay))
                action_placements = placements[count:]
            except (OSError, ValueError) as error:
                problems.append(f"{action}: {error}")
        duration = max((p.delay + p.emitter.duration + (p.emitter.life or 1) for p in action_placements), default=0)
        return tuple(placements), len(placements) - len(action_placements), duration, tuple(dict.fromkeys(problems))

    def render_effects(self, choice, kind, width, height, turn, attachments, action, elapsed):
        placements, bone_count, duration, problems = self.scene(choice, attachments, action)
        canvas, depth, frame, axes = self.base_image(choice, kind, width, height, turn, bool(placements))
        if not placements:
            return canvas.tobytes(), problems, False
        action_time = elapsed % max(1.5, duration + .3)

        streams = [p.particles(elapsed if i < bone_count else action_time, axes, frame) for i, p in enumerate(placements)]
        return self.compose(canvas, depth, streams), problems, True
