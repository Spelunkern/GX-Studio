"""Read-only GX asset scenes and their authored resource references."""

from dataclasses import dataclass, replace
from functools import lru_cache
from math import cos, isfinite, sin
from os import walk
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .catalog import decode_text
from .client_model_data import AltCatalog, TextureCatalog
from .effect_preview import EffectPreviewRenderer, IDENTITY, read_effect_mesh, read_eft
from .equipment_models import ModelAssets, ModelChoice, ModelFormatError, Surface, Cursor, armor_pose, basename, cape_rows
from .equipment_models import dds_name
from .itm_catalog import ItmCatalog, name_at
from .mlt_catalog import MltCatalog
from .model_render import (
    BACKGROUND, PreparedSurface, camera_axes, fit_frame, read_animation, read_mesh, render_surfaces,
)
from .mon_catalog import MonCatalog, slot_text
from .weapon_preview import EnchantLayer, read_seff
from .world_assets import WORLD_VERTEX, WorldPart, read_dg, read_mani, read_smod, read_vani, read_wtr

MODEL_EXTENSIONS = frozenset((".3dc", ".3do", ".3de", ".ani", ".alt", ".smod", ".dg", ".vani", ".mani",
                              ".wtr", ".eft", ".seff", ".pc"))
IMAGE_EXTENSIONS = frozenset((".dds", ".png", ".tga", ".bmp", ".jpg", ".jpeg", ".cur", ".ico"))
FONT_EXTENSIONS = frozenset((".ttf", ".otf"))
ASSET_EXTENSIONS = MODEL_EXTENSIONS | IMAGE_EXTENSIONS | FONT_EXTENSIONS | {".ogg", ".ctl"}
MAX_MODEL_BYTES = 32 * 1024 * 1024


def read_asset(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_MODEL_BYTES + 1)
    if len(raw) > MAX_MODEL_BYTES:
        raise ModelFormatError("The asset exceeds 32 MiB.")
    return raw


class ViewerAssets(ModelAssets):
    def __init__(self, source, workspace):
        self.source = source
        roots = {"character", "item", "effect", "entity", "world", "monster", "npc", "vehicle", "cloak"}
        root = next((path.parent for path in source.parents if path.name.casefold() in roots), workspace or source.parent)
        if root.name.casefold() == "cloak":
            root = root.parent
        super().__init__(root)
        self.local = ModelAssets(source.parent)
        self.nearby = ModelAssets(source.parent.parent)

    @lru_cache(maxsize=4096)
    def resolve(self, name):
        path = Path(name)
        if path.is_absolute():
            return path if path.is_file() else None
        found = super().resolve(name)
        if found is None and name.startswith("Effect/"):
            local = self.local if self.source.parent.name.casefold() != "3de" else self.nearby
            found = local.resolve(name.removeprefix("Effect/"))
        return found

    @lru_cache(maxsize=512)
    def texture_path(self, name, folder="", *, search=False):
        name = basename(name)
        if not name:
            return ""
        dds = dds_name(name)
        for candidate in dict.fromkeys((dds, name)):
            if folder and (path := self.resolve(f"{folder}/{candidate}")):
                return str(path)
            for relative in (f"DDS/{candidate}", candidate):
                if path := self.local.resolve(relative):
                    return str(path)
            if path := self.nearby.resolve(f"DDS/{candidate}"):
                return str(path)
        if search:
            return str(texture_index(self.root).get(Path(dds).stem.casefold(), ""))
        return ""


@lru_cache(maxsize=2)
def texture_index(root):
    textures = {}
    for parent, _, names in walk(root):
        for name in names:
            if not name.casefold().endswith(".dds"):
                continue
            path = Path(parent) / name
            key = path.stem.casefold()
            relative = path.relative_to(root).as_posix().casefold()
            old = textures.get(key)
            if old is None or relative.startswith("entity/texture/") or (
                relative.startswith("world/dungeon/")
                and not old.relative_to(root).as_posix().casefold().startswith("entity/texture/")
            ):
                textures[key] = path
    return textures


@lru_cache(maxsize=8)
def mesh_references(folder):
    output = {}
    for path in sorted(folder.iterdir()):
        ext = path.suffix.casefold()
        if ext not in (".mlt", ".mon", ".itm", ".it2") or not path.is_file():
            continue
        try:
            if ext == ".mon":
                refs = (
                    (decode_text(mesh), decode_text(texture), True, f"ANI/{basename(slot_text(row.animations[6]))}")
                    for row in MonCatalog.open(path).records for mesh, texture in row.parts
                )
            else:
                table = (MltCatalog if ext == ".mlt" else ItmCatalog).open(path)
                pose = armor_pose(path.stem.split("_", 1)[0].casefold()) if ext == ".mlt" else ""
                refs = (
                    (name_at(table.meshes, row.mesh), name_at(table.textures, row.texture),
                     row.alpha == 0 if ext == ".mlt" else row.cutout, pose)
                    for row in table.records
                )
            for mesh, texture, cutout, pose in refs:
                if mesh and texture:
                    output.setdefault(basename(mesh).casefold(), {}).setdefault((basename(texture), cutout), pose)
        except (OSError, ValueError):
            continue
    return output


@lru_cache(maxsize=2)
def effect_mesh_references(root):
    output = {}
    for path in sorted(root.iterdir()):
        if path.suffix.casefold() != ".eft" or not path.is_file():
            continue
        try:
            library = read_eft(read_asset(path))
        except (OSError, ValueError):
            continue
        for e in library.emitters:
            if 0 <= e.mesh_index < len(library.meshes) and not e.billboard:
                key = basename(library.meshes[e.mesh_index]).casefold()
                output.setdefault(key, set()).update(
                    basename(library.textures[i]) for i in e.textures if 0 <= i < len(library.textures)
                )
    return output


@dataclass(frozen=True, slots=True)
class Layer:
    part: WorldPart
    texture: str
    lightmap: str = ""
    cutout: bool = True


@dataclass(eq=False, frozen=True, slots=True)
class Scene:
    model: ModelChoice | None = None
    layers: tuple[Layer, ...] = ()
    frame_ms: float = 1000 / 30
    mesh_3de: object = None
    texture_3de: str = ""
    clip_path: Path | None = None
    rotor: tuple | None = None
    water: tuple | None = None
    eft: tuple = ()
    seff: tuple = ()
    information: str = ""

    @property
    def animated(self):
        return bool(self.clip_path or (self.rotor and self.rotor[1]) or self.eft or self.seff
                    or (self.mesh_3de is not None and len(self.mesh_3de.keys) > 1)
                    or (self.water and self.frame_ms > 0 and len(self.water[0]) > 1)
                    or any(len(layer.part.vertices) > 1 for layer in self.layers))


@dataclass(frozen=True, slots=True)
class ModelDocument:
    renderer: object
    variants: tuple[tuple[str, Scene], ...]


def local_reference(folder, name):
    return ModelAssets(folder).resolve(name)


def mesh_scenes(source, assets, raw):
    ext = source.suffix.casefold()
    kind = "rigid" if ext == ".3do" else "skin"
    if kind == "skin" and any(part.casefold() == "cloak" for part in source.parts) and source.parent.name.casefold() == "3dc":
        kind = "cloth"
    mesh = read_mesh(raw, kind)
    folder = source.parent.parent if source.parent.name.casefold() in ("3dc", "3do", "static") else source.parent
    if folder.name.casefold() == "3dc":
        folder = folder.parent
    if any(part.casefold() == "cloak" for part in source.parts):
        variants = {}
        for path in sorted(folder.glob("*")):
            if path.suffix.casefold() != ".pc" or not path.is_file():
                continue
            try:
                for _, scene in cloak_scenes(path, ViewerAssets(path, assets.root), read_asset(path)):
                    for surface in scene.model.surfaces:
                        if Path(surface.mesh) == source and surface.texture:
                            variants.setdefault(surface.texture, replace(
                                scene, model=ModelChoice(source.name, (surface,)),
                            ))
            except (OSError, ValueError):
                continue
        if variants:
            return tuple((Path(name).name, scene) for name, scene in variants.items())
    refs = dict(mesh_references(folder).get(source.name.casefold(), {}))
    header_texture = Cursor(raw).name() if ext == ".3do" else ""
    if header_texture:
        refs.setdefault((basename(header_texture), True), "")
    if not refs:
        refs[(source.stem + ".dds", True)] = ""
    variants = []
    seen = set()
    detail = f"{len(mesh.vertices):,} vertices  ·  {len(mesh.faces):,} triangles"
    for (name, cutout), pose in refs.items():
        pose = local_reference(folder, pose) if pose and not pose.endswith("/") else None
        texture = assets.texture_path(name)
        key = texture, cutout, pose
        if key in seen:
            continue
        seen.add(key)
        surface = Surface(str(source), texture, kind, cutout, True, str(pose) if pose else "")
        status = detail if texture else f"{detail}  ·  Texture unavailable"
        variants.append((Path(texture).name if texture else "Untextured",
                         Scene(model=ModelChoice(source.name, (surface,)), information=status)))
    return tuple(variants)


def effect_scenes(source, assets, raw):
    if source.suffix.casefold() == ".seff":
        return tuple((f"Record {index}", Scene(seff=tuple(enumerate(layers)), information=f"{len(layers)} emitters"))
                     for index, layers in sorted(read_seff(raw).items()) if layers)
    if source.suffix.casefold() == ".eft":
        library = read_eft(raw)
        sequences = tuple(
            (f"Sequence {index} · {name}".rstrip(" ·"),
             Scene(eft=(library, records), information=f"{len(records)} emitter instances"))
            for index, (name, records) in enumerate(zip(library.sequence_names, library.sequences)) if records
        )
        referenced = {index for records in library.sequences for index, _ in records}
        standalone = tuple((index, 0) for index in range(len(library.emitters)) if index not in referenced)
        if standalone:
            label = "Unassigned Emitters" if sequences else "All Emitters"
            sequences += ((label, Scene(eft=(library, standalone), information=f"{len(standalone)} emitters")),)
        return sequences
    mesh = read_effect_mesh(raw)
    name = Cursor(raw).name()
    refs = [basename(name)] if name else []
    if source.parent.name.casefold() == "3de":
        refs.extend(sorted(effect_mesh_references(source.parent.parent).get(source.name.casefold(), ())))
    detail = f"{len(mesh.vertices):,} vertices  ·  {len(mesh.faces):,} triangles  ·  {len(mesh.keys)} keyframes"
    textures = tuple(dict.fromkeys(p for ref in refs
                                   if (p := assets.texture_path(ref, "Effect/DDS"))))
    return tuple((Path(texture).name if texture else "Untextured", Scene(
        mesh_3de=mesh, texture_3de=texture, information=detail if texture else f"{detail}  ·  Texture unavailable",
    )) for texture in textures or ("",))


def effect_components(scene):
    if scene.eft:
        library, records = scene.eft
        by_emitter = {}
        for index, delay in records:
            by_emitter.setdefault(index, []).append((index, delay))
        components = []
        for index, instances in by_emitter.items():
            name = library.emitters[index].name if 0 <= index < len(library.emitters) else ""
            label = f"Emitter {index} · {name}".rstrip(" ·")
            component = replace(scene, eft=(library, tuple(instances)), information=f"{len(instances)} emitter instances")
            components.append((label, component))
        return (("Full Effect", scene), *components)
    if scene.seff:
        return (("Full Effect", scene), *(
            (f"Emitter {index} · {basename(emitter.texture)}".rstrip(" ·"),
             replace(scene, seff=((index, emitter),), information="1 emitter"))
            for index, emitter in scene.seff
        ))
    return ()


def animation_scenes(source, raw):
    if source.suffix.casefold() == ".ani":
        clip = read_animation(raw)
        return ((source.name, Scene(clip_path=source,
                                    information=f"{len(clip.bones)} bones  ·  Frames {clip.start}–{clip.end}")),)
    variants = []
    for index, row in enumerate(AltCatalog(raw).records):
        name = decode_text(row.name)
        if not name:
            continue
        mode, next_action, *metadata = row.values()
        path = local_reference(source.parent, f"ANI/{basename(name)}")
        info = f"Mode {mode}  ·  Next action {next_action}  ·  Keys " + ", ".join(f"{value:g}" for value in metadata)
        variants.append((f"{index} · {name}", Scene(clip_path=path, information=info if path else info + "  ·  ANI unavailable")))
    return tuple(variants)


def cloak_scenes(source, assets, raw):
    prefix = source.stem.split("_")[-1].casefold()
    race = source.parent.name
    pose = assets.resolve(f"Character/{race}/{armor_pose(prefix)}") if prefix else None
    texture_file = assets.resolve(f"cloak/ClothTexture/CLOTH_TEXTHRE_{prefix[:2].upper()}.CTL")
    names = ctl_names(read_asset(texture_file)) if texture_file else ()
    variants = []
    for index, row in enumerate(cape_rows(raw)):
        if row is None:
            continue
        texture_name = names[row.texture] if 0 <= row.texture < len(names) else ""
        texture = assets.texture_path(texture_name, f"cloak/ClothTexture/{prefix[:2].upper()}")
        body = local_reference(source.parent, f"3DC/{row.mesh}")
        if not body:
            body = source.parent / "3DC" / row.mesh
        parts = [Surface(str(body), texture, "cloth", True, True, str(pose) if pose else "", row.pins, row.grid)]
        if row.shoulder and (shoulder := local_reference(source.parent, f"3DC/Static/{row.shoulder}")):
            parts.append(Surface(str(shoulder), texture, "skin", False, True, str(pose) if pose else ""))
        label = f"{index} · {texture_name or row.mesh}"
        detail = f"{row.grid[0]} × {row.grid[1]} cloth grid  ·  {len(row.pins)} pins"
        variants.append((label, Scene(model=ModelChoice(label, tuple(parts)), information=detail)))
    return tuple(variants)


def ctl_names(raw):
    return tuple(map(decode_text, TextureCatalog(raw).records))


def cloth_texture_paths(source, workspace):
    assets = ViewerAssets(source, workspace)
    race = source.stem.split("_")[-1]
    return tuple((f"{index} · {name}", assets.texture_path(name, f"cloak/ClothTexture/{race}"))
                 for index, name in enumerate(ctl_names(read_asset(source))) if name)


def world_scenes(source, assets, raw):
    ext = source.suffix.casefold()
    if ext == ".mani":
        rotation = read_mani(raw)
        axis, speed = rotation
        detail = f"Axis {', '.join(f'{value:g}' for value in axis)}  ·  {speed:g} rad/s"
        return ((source.name, Scene(rotor=rotation, information=detail)),)
    if ext == ".wtr":
        scale, argb, interval, frames = read_wtr(raw)
        paths = tuple(assets.texture_path(name, "Entity/Water") for name in frames)
        detail = f"{len(frames)} frames  ·  Tile size {scale:g}  ·  ARGB {argb:08X}"
        return ((source.name, Scene(water=(paths, argb, scale), frame_ms=interval, information=detail)),)
    interval = 0
    if ext == ".smod":
        parts = read_smod(raw)
    elif ext == ".dg":
        parts = read_dg(raw)
    else:
        parts, interval = read_vani(raw)
    lightmap = f"{source.stem}/{source.stem}_L"
    layers = tuple(Layer(
        part, assets.texture_path(part.texture, "Entity/Texture", search=True),
        str(assets.local.resolve(f"{lightmap}{part.lightmap}.dds") or "") if part.lightmap >= 0 else "",
        cutout=ext != ".dg",
    ) for part in parts if part.vertices.shape[1] and len(part.faces))
    vertices = sum(part.part.vertices.shape[1] for part in layers)
    triangles = sum(len(part.part.faces) for part in layers)
    detail = f"{vertices:,} vertices  ·  {triangles:,} triangles"
    if interval:
        detail += f"  ·  {interval:g} ms/frame"
    return ((source.name, Scene(layers=layers, frame_ms=interval, information=detail)),)


def open_model(source, workspace):
    source = source.absolute()
    assets = ViewerAssets(source, workspace)
    raw = read_asset(source)
    ext = source.suffix.casefold()
    if ext in (".3do", ".3dc"):
        variants = mesh_scenes(source, assets, raw)
    elif ext in (".3de", ".eft", ".seff"):
        variants = effect_scenes(source, assets, raw)
    elif ext in (".ani", ".alt"):
        variants = animation_scenes(source, raw)
    elif ext == ".pc":
        variants = cloak_scenes(source, assets, raw)
    else:
        variants = world_scenes(source, assets, raw)
    if not variants:
        raise ValueError("This table contains no previewable entries.")
    return ModelDocument(AssetRenderer(assets), variants)


def axes_matrix(axis, angle):
    axis = np.asarray(axis, dtype=np.float32)
    norm = np.linalg.norm(axis)
    if not norm:
        return IDENTITY[:3, :3]
    x, y, z = axis / norm
    c, s = cos(angle), sin(angle)
    d = 1 - c
    return np.array(((c+x*x*d, x*y*d+z*s, x*z*d-y*s),
                     (y*x*d-z*s, c+y*y*d, y*z*d+x*s),
                     (z*x*d+y*s, z*y*d-x*s, c+z*z*d)), dtype=np.float32)


def draw_lines(points, edges, frame, kind, width, height, turn, labels=()):
    axes = camera_axes(kind, turn, frame.extent)
    points = (points - frame.center) @ axes
    depth = np.maximum(.01, frame.distance - points[:, 2])
    pixel = points[:, :2] * (frame.focal / depth[:, None])
    pixel[:, 0] += width * .5
    pixel[:, 1] = height * .5 - pixel[:, 1]
    valid = np.isfinite(pixel).all(axis=1)
    pixel = np.clip(pixel, -32768, 32768)
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    for index, parent, color in edges:
        if valid[parent] and valid[index]:
            draw.line((*pixel[parent], *pixel[index]), fill=color, width=2)
    for x, y in pixel[valid]:
        draw.ellipse((x-2, y-2, x+2, y+2), fill="#30343b")
    for index, label in labels:
        draw.text(tuple(pixel[index] + 5), label, fill="#30343b")
    return image.tobytes()


class AssetRenderer(EffectPreviewRenderer):
    def __init__(self, assets):
        super().__init__(assets, assets.root)
        self.effects = self
        self.static_world = lru_cache(maxsize=1)(self.static_world)

    def static_world(self, scene):
        return tuple(self.world_surface(layer, 0) for layer in scene.layers)

    @lru_cache(maxsize=8)
    def clip(self, path):
        return read_animation(read_asset(path))

    @lru_cache(maxsize=16)
    def scene_frame(self, scene, kind, width, height):
        if scene.clip_path:
            clip = self.clip(scene.clip_path)
            points = [clip.pose(t)[:, 3, :3] for t in np.linspace(clip.start, clip.end, 6)]
        elif scene.rotor:
            points = [np.array(((-1, -1, -1), (1, 1, 1)), dtype=np.float32) * 1.25]
        elif scene.model:
            return self.frame(scene.model, kind, width, height)
        elif scene.mesh_3de:
            mesh = scene.mesh_3de
            points = [mesh.vertices, *(frame[:, :3] for frame in mesh.frames)]
        elif scene.water:
            points = [WATER_VERTICES["position"]]
        else:
            points = [l.part.vertices["position"].reshape(-1, 3) for l in scene.layers]
        if not points or not any(len(p) for p in points):
            raise ModelFormatError("No geometry is available for this selection")
        coordinates = np.concatenate(points)
        coordinates = coordinates[np.isfinite(coordinates).all(axis=1)]
        if not len(coordinates):
            raise ModelFormatError("Invalid geometry")
        return fit_frame(coordinates, kind, width, height)

    def world_surface(self, layer, index):
        part = layer.part
        data = part.vertices[index % len(part.vertices)]
        faces = part.faces[(part.faces < len(data)).all(axis=1)]
        lightmap = self.texture(layer.lightmap) if layer.lightmap else None
        position, normals, uv = data["position"], data["normal"], data["uv"]
        uv2 = data["uv2"] if lightmap is not None else None
        valid = np.isfinite(position).all(axis=1) & np.isfinite(uv).all(axis=1)
        if uv2 is not None:
            valid &= np.isfinite(uv2).all(axis=1)
        if not valid.all():
            faces = faces[valid[faces].all(axis=1)]
            position = np.where(valid[:, None], position, 0)
        if not np.isfinite(normals).all():
            normals = np.where(np.isfinite(normals), normals, 0)
        return PreparedSurface(Surface("", "", "rigid", layer.cutout, True), position, normals,
                               uv, faces, self.texture(layer.texture), uv2, lightmap)

    @lru_cache(maxsize=40)
    def water_texture(self, name, color):
        rgba = self.texture(name).copy()
        tint = np.array(((color >> 16) & 255, (color >> 8) & 255, color & 255), dtype=np.float32) / 255
        alpha = rgba[:, :, 3:4] * (((color >> 24) & 255) / (255 * 255))
        rgba[:, :, :3] = rgba[:, :, :3] * tint * alpha + np.asarray(BACKGROUND) * (1 - alpha)
        return rgba

    @lru_cache(maxsize=12)
    def placements(self, scene):
        particles, issues = [], []
        if scene.eft:
            library, records = scene.eft
            templates = {}
            for index, delay in records:
                if index not in templates:
                    try:
                        templates[index] = self.placement(library, index, IDENTITY, 0)
                    except (OSError, ValueError) as error:
                        issues.append(f"Emitter {index} · {error}")
                        templates[index] = None
                if (template := templates[index]) is not None:
                    particles.append(replace(template, delay=max(0, delay)))
        else:
            for index, emitter in scene.seff:
                name = f"Effect/DDS/{basename(emitter.texture)}"
                if not emitter.supported():
                    issues.append(f"Emitter {index} · Unsupported particle settings")
                elif self.assets.resolve(name) is None:
                    issues.append(f"Emitter {index} · Missing {name}")
                else:
                    try:
                        particles.append(EnchantLayer(emitter, self.texture(name), index * 977))
                    except (OSError, ValueError) as error:
                        issues.append(f"Emitter {index} · {error}")
        return tuple(particles), tuple(issues)

    @lru_cache(maxsize=16)
    def effect_frame(self, scene, width, height):
        radius = 1.0
        placements, _ = self.placements(scene)
        for p in placements:
            e = p.emitter
            if scene.eft:
                size = float(np.abs(e.sizes[:, :2]).max()) if len(e.sizes) else .3
                travel = np.linalg.norm(e.vectors[0]) + np.linalg.norm(e.vectors[2])
                travel += float(np.abs(e.vectors[3:]).max()) * e.life
                mesh_scale = float(np.abs(p.mesh.vertices).max()) if p.mesh is not None else 1
                radius = max(radius, float(travel) + size * mesh_scale)
            else:
                radius = max(radius, np.linalg.norm(e.origin) + abs(e.extent) * .12
                             + 2 * e.half_size + abs(e.speed) * e.lifetime_ms / 1000)
        radius = min(5000, radius)
        points = np.array(((0, 0, -radius), (0, 0, radius), (radius, 0, 0), (-radius, 0, 0),
                           (0, radius, 0), (0, -radius, 0)), dtype=np.float32)
        return fit_frame(points, "npc", width, height)

    def draw_particle_scene(self, scene, width, height, turn, elapsed, zoom):
        placements, issues = self.placements(scene)
        frame = self.effect_frame(scene, width, height)
        frame = replace(frame, focal=frame.focal * zoom)
        axes = camera_axes("npc", turn, frame.extent)
        canvas = np.full((height, width, 3), (85, 85, 88), dtype=np.uint8)
        depth = np.zeros((height, width), dtype=np.float32)
        if scene.eft and placements:
            cycle = max(p.delay + p.emitter.duration + (p.emitter.life or 1) for p in placements)
            if any(not p.emitter.loop for p in placements):
                elapsed %= max(1.5, cycle + .3)
        pixels = self.compose(canvas, depth, [p.particles(elapsed, axes, frame) for p in placements], include_each=True)
        return pixels, issues, bool(placements)

    def render_effects(self, scene, kind, width, height, turn, playing, paused_at, zoom, elapsed):
        if not playing:
            elapsed = paused_at
        if scene.eft or scene.seff:
            pixels, issues, animated = self.draw_particle_scene(scene, width, height, turn, elapsed, zoom)
            return pixels, issues, playing and animated
        if not (scene.layers or scene.model or scene.mesh_3de or scene.clip_path or scene.rotor or scene.water):
            return "No 3D model available", (), False
        frame = self.scene_frame(scene, kind, width, height)
        frame = replace(frame, focal=frame.focal * zoom)
        if scene.clip_path:
            clip = self.clip(scene.clip_path)
            points = clip.pose(clip.start + (elapsed * 30) % max(1, clip.end - clip.start + 1))[:, 3, :3]
            edges = ((i, bone[0], "#3e648d") for i, bone in enumerate(clip.bones) if 0 <= bone[0] < len(points))
            return draw_lines(points, edges, frame, kind, width, height, turn), (), playing
        if scene.rotor:
            axis, speed = scene.rotor
            points = np.array(((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))) @ axes_matrix(axis, elapsed * speed)
            edges = ((1, 0, "#ac3437"), (2, 0, "#34734d"), (3, 0, "#315fa0"))
            return draw_lines(points, edges, frame, kind, width, height, turn,
                              ((1, "X"), (2, "Y"), (3, "Z"))), (), playing and scene.animated
        if scene.model:
            surfaces = tuple(self.prepare(spec) for spec in scene.model.surfaces)
        elif scene.mesh_3de:
            mesh = scene.mesh_3de
            points, uv = mesh.sample(elapsed, True)
            normals = np.zeros_like(points)
            surfaces = (PreparedSurface(Surface("", "", "rigid", True, True),
                                        points, normals, uv, mesh.faces, self.texture(scene.texture_3de)),)
        elif scene.water:
            paths, color, scale = scene.water
            index = int(elapsed * 1000 / scene.frame_ms) % len(paths) if paths and scene.frame_ms else 0
            texture = self.water_texture(paths[index] if paths else "", color)
            uv = WATER_VERTICES["uv"] * (64 / scale)
            surfaces = (PreparedSurface(Surface("", "", "rigid", two_sided=True),
                                        WATER_VERTICES["position"], WATER_VERTICES["normal"], uv, WATER_FACES, texture),)
        elif scene.frame_ms > 0:
            index = int(elapsed * 1000 / scene.frame_ms)
            surfaces = tuple(self.world_surface(layer, index) for layer in scene.layers)
        else:
            surfaces = self.static_world(scene)
        image = render_surfaces(surfaces, frame, kind, width, height, turn)
        return image.tobytes(), (), playing and scene.animated


WATER_VERTICES = np.zeros(4, dtype=WORLD_VERTEX)
WATER_VERTICES["position"] = ((-1, 0, -1), (1, 0, -1), (1, 0, 1), (-1, 0, 1))
WATER_VERTICES["normal"] = (0, 1, 0)
WATER_VERTICES["uv"] = ((0, 0), (1, 0), (1, 1), (0, 1))
WATER_FACES = np.array(((0, 1, 2), (0, 2, 3)), dtype=np.uint16)
