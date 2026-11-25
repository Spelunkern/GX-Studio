"""Model tables and asset paths used by the GX equipment renderer."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from struct import Struct

from .catalog import decode_text
from .itm_catalog import ItmCatalog, name_at
from .mlt_catalog import MltCatalog
from .mon_catalog import MonCatalog, slot_text

I32 = Struct("<i")
PC_ROW = Struct("<6I")
PC_PIN = Struct("<III3f")
GLOW_NAMES = ("Blue glow", "Red glow", "Green glow", "White glow")
WEAPON_TYPES = dict(zip(range(1, 16), range(1, 16)))
WEAPON_TYPES.update(zip(range(45, 66), (1, 2, 3, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 10, 11, 12, 12, 13, 13, 14, 15)))
WEAPON_TYPES.update(dict.fromkeys((19, 34, 69, 75, 84, 90), 19))
ARMOR_TYPES = {
    family: part
    for part, families in (
        ("helmet", (16, 31, 66, 72, 81, 87)),
        ("upper", (17, 32, 67, 73, 82, 88)),
        ("lower", (18, 33, 68, 74, 83, 89)),
        ("hand", (20, 35, 70, 76, 85, 91)),
        ("foot", (21, 36, 71, 77, 86, 92)),
    )
    for family in families
}
CAPE_TYPES = (24, 39)
OTHER_EQUIPPABLE_TYPES = (22, 23, 37, 38, 40, 41, 42, 96, 97, 120, 121, 150)


@dataclass(frozen=True, slots=True)
class Body:
    race: str
    prefix: str
    role: str
    jobs: tuple[int, ...]
    country: int

    def archetype(self, female: bool) -> str:
        return self.prefix + ("w" if female else "m") + self.role

    @property
    def label(self) -> str:
        return f"{self.race} · {dict(f='Fighter', r='Rogue', m='Magic')[self.role]}"


HUMAN = Body("Human", "hu", "f", (0, 1), 0)
HUMAN_MAGIC = Body("Human", "hu", "m", (5,), 0)
ELF = Body("Elf", "el", "r", (2, 3), 1)
ELF_MAGIC = Body("Elf", "el", "m", (4,), 1)
DEATH = Body("DeathEater", "de", "f", (0, 1), 3)
DEATH_ROGUE = Body("DeathEater", "de", "r", (3,), 3)
VILE = Body("Vile", "vi", "r", (2,), 4)
VILE_MAGIC = Body("Vile", "vi", "m", (4, 5), 4)
BODIES = (HUMAN, ELF, ELF_MAGIC, HUMAN_MAGIC, DEATH, VILE, DEATH_ROGUE, VILE_MAGIC)


def model_kind(family: int) -> str:
    if family in WEAPON_TYPES:
        return "weapon"
    if family in ARMOR_TYPES:
        return "armor"
    if family in CAPE_TYPES:
        return "cape"
    return {42: "mount", 121: "wing", 120: "pet"}.get(family, "")


def has_equipment_durability(family: int) -> bool:
    return family in WEAPON_TYPES or family in ARMOR_TYPES or family in CAPE_TYPES or family in OTHER_EQUIPPABLE_TYPES


def eligible_bodies(family: int, country: int, classes: tuple[int, ...]) -> tuple[Body, ...]:
    if country in (0, 1, 3, 4):
        countries = (country,)
    elif country == 2:
        countries = (0, 1)
    elif country == 5:
        countries = (3, 4)
    elif family in (24, 39):
        countries = (0, 1) if family == 24 else (3, 4)
    else:
        countries = (0, 1, 3, 4)
    allowed = tuple(body for body in BODIES if body.country in countries)
    return tuple(body for body in allowed if any(classes[job] for job in body.jobs)) or allowed


class ModelFormatError(ValueError):
    pass


class Cursor:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def take(self, size: int) -> bytes:
        if size < 0 or size > len(self.data) - self.offset:
            raise ModelFormatError("Truncated model data")
        start = self.offset
        self.offset += size
        return self.data[start:self.offset]

    def unpack(self, layout: Struct):
        return layout.unpack(self.take(layout.size))

    def count(self, stride: int) -> int:
        value = self.unpack(I32)[0]
        if not 0 <= value <= (len(self.data) - self.offset) // stride:
            raise ModelFormatError("Invalid model table count")
        return value

    def name(self) -> str:
        return decode_text(self.take(self.count(1)))

    def names(self) -> tuple[str, ...]:
        return tuple(self.name() for _ in range(self.count(4)))

    def finish(self):
        if self.offset != len(self.data):
            raise ModelFormatError("Unexpected bytes at the end of the model table")


@dataclass(frozen=True, slots=True)
class Surface:
    mesh: str
    texture: str
    mesh_kind: str
    cutout: bool = False
    two_sided: bool = False
    pose: str = ""
    pins: tuple[tuple[int, int, tuple[float, float, float]], ...] = ()
    cloth_grid: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class ModelChoice:
    name: str
    surfaces: tuple[Surface, ...]
    glow: int = -1
    table: str = ""


def basename(value: str) -> str:
    name = PurePosixPath(value.replace("\\", "/")).name
    return name if name not in (".", "..") else ""


def is_vehicle_mon(path: Path) -> bool:
    return path.suffix.casefold() == ".mon" and (
        path.parent.name.casefold() == "vehicle" or path.stem.casefold().startswith("vehicle")
    )


def dds_name(value: str) -> str:
    name = basename(value)
    return PurePosixPath(name).with_suffix(".dds").name if name else ""


def entry(names: tuple[str, ...], index: int) -> str:
    return basename(names[index]) if 0 <= index < len(names) else ""


def table_rows(raw: bytes, kind: str) -> tuple[tuple[str, str, bool, int] | None, ...]:
    try:
        table = ItmCatalog(raw) if kind == "weapon" else MltCatalog(raw)
    except ValueError as error:
        raise ModelFormatError(str(error)) from error
    if kind == "weapon":
        return tuple(
            (mesh, basename(name_at(table.textures, row.texture)), row.cutout, row.glow) if mesh else None
            for row in table.records
            for mesh in (basename(name_at(table.meshes, row.mesh)),)
        )
    return tuple(
        (mesh, basename(name_at(table.textures, row.texture)), row.alpha == 0, -1) if mesh else None
        for row in table.records
        for mesh in (basename(name_at(table.meshes, row.mesh)),)
    )


def armor_choice(mesh, texture, cutout, folder, pose, filename=""):
    mesh, texture = basename(mesh), dds_name(texture)
    if not mesh:
        return None
    prefix = f"{folder}/" if folder else ""
    return ModelChoice(mesh, (Surface(
        f"{prefix}3DC/{mesh}", f"{prefix}DDS/{texture}" if texture else "", "skin",
        cutout, two_sided=True, pose=pose,
    ),), table=filename)


def armor_pose(prefix):
    idle = "000_nomal" if prefix.startswith("vi") else "000_normal"
    return f"ANI/{prefix}_{idle}.ANI" if prefix else ""


@dataclass(frozen=True, slots=True)
class CapeChoice:
    mesh: str
    shoulder: str
    texture: int
    pins: tuple[tuple[int, int, tuple[float, float, float]], ...]
    grid: tuple[int, int]


def cape_rows(raw: bytes) -> tuple[CapeChoice | None, ...]:
    reader = Cursor(raw)

    def names() -> tuple[str, ...]:
        output = [""] * reader.count(132)
        for _ in range(len(output)):
            index = reader.unpack(I32)[0]
            name = decode_text(reader.take(128))
            if not 0 <= index < len(output):
                raise ModelFormatError("Invalid PC mesh index")
            output[index] = name
        return tuple(output)

    bodies = names()
    records = []
    for _ in range(reader.count(524)):
        body, texture, mode, shoulder, columns, rows = reader.unpack(PC_ROW)
        columns += 1
        rows += 1
        if not (2 <= columns <= 32 and 2 <= rows <= 128):
            raise ModelFormatError("Invalid PC cloth grid")
        reader.take(20)  # Emblem layout and reserved word.
        pins = []
        for _ in range(20):
            active, vertex, bone, x, y, z = reader.unpack(PC_PIN)
            if active:
                if vertex >= columns * rows or bone >= 256:
                    raise ModelFormatError("Invalid PC pin")
                pins.append((vertex, bone, (x, y, z)))
        records.append((body, texture, mode, shoulder, tuple(pins), (columns, rows)))
    shoulders = names()
    reader.finish()
    return tuple(
        CapeChoice(entry(bodies, body), entry(shoulders, shoulder), texture, pins, grid)
        if mode in (0, 1) and entry(bodies, body) else None
        for body, texture, mode, shoulder, pins, grid in records
    )


def mon_rows(raw: bytes, directory: str, filename: str, *, max_index: int | None = 255) -> dict[int, ModelChoice]:
    records = MonCatalog(raw).records
    return {index: choice for index, record in enumerate(records)
            if (max_index is None or index <= max_index)
            and (choice := mon_model_choice(record, directory, filename)) is not None}


def mon_model_choice(record, directory: str, filename: str) -> ModelChoice | None:
    prefix = f"{directory}/" if directory else ""
    pose = basename(slot_text(record.animations[6]))
    parts = []
    for mesh_name, texture_name in record.parts:
        mesh = basename(decode_text(mesh_name))
        texture = dds_name(decode_text(texture_name))
        if mesh:
            parts.append(Surface(f"{prefix}3DC/{mesh}", f"{prefix}DDS/{texture}" if texture else "",
                                 "skin", True, pose=f"{prefix}ANI/{pose}" if pose else ""))
    if parts:
        return ModelChoice(record.display_name or Path(parts[0].mesh).name, tuple(parts), table=filename)


class ModelAssets:
    def __init__(self, root: Path):
        self.root = root

    @lru_cache(maxsize=512)
    def directory_names(self, path: Path) -> dict[str, Path]:
        try:
            return {child.name.casefold(): child for child in path.iterdir()}
        except OSError:
            return {}

    @lru_cache(maxsize=4096)
    def resolve(self, name: str) -> Path | None:
        path = self.root
        for part in name.split("/"):
            if not part or part in (".", ".."):
                return None
            exact = path / part
            if exact.exists():
                path = exact
                continue
            match = self.directory_names(path).get(part.casefold())
            if match is None:
                return None
            path = match
        return path if path.is_file() else None

    def read(self, name: str) -> bytes:
        path = self.resolve(name)
        if path is None:
            raise FileNotFoundError(name)
        limit = 32 * 1024 * 1024
        with path.open("rb") as file:
            data = file.read(limit + 1)
        if len(data) > limit:
            raise ModelFormatError(f"{name} exceeds the model size limit")
        return data

    @lru_cache(maxsize=512)
    def skin_version(self, name: str) -> str:
        path = self.resolve(f"3DC/{basename(name)}") if name else None
        if path is not None:
            try:
                with path.open("rb") as stream:
                    header = stream.read(4)
                if len(header) == 4:
                    return "6" if I32.unpack(header)[0] == 444 else "5"
            except OSError:
                pass
        return "—"

    @lru_cache(maxsize=8)
    def npc_choices(self) -> dict[int, ModelChoice]:
        filename = "Npc/npc.MON"
        return mon_rows(self.read(filename), "Npc", filename, max_index=None) if self.resolve(filename) else {}

    @lru_cache(maxsize=8)
    def npc_portraits(self) -> dict[int, Path]:
        interface = self.directory_names(self.root).get("interface", self.root / "interface")
        directory = self.directory_names(interface).get("npcface", interface / "NpcFace")
        portraits = {}
        for path in sorted(self.directory_names(directory).values()):
            if path.stem.isascii() and path.stem.isdecimal() and path.suffix.casefold() in {".png", ".tga", ".bmp"}:
                portraits.setdefault(int(path.stem), path)
        return portraits

    @lru_cache(maxsize=128)
    def choices(self, family: int, body: Body = HUMAN, female: bool = False, fit: int = 1) -> dict[int, ModelChoice]:
        kind = model_kind(family)
        if kind == "weapon":
            return self.weapon_choices(family)
        if kind == "armor":
            return self.armor_choices(family, body, female)
        if kind == "cape":
            return self.cape_choices(body, female, fit)
        if kind == "mount":
            filename = f"Vehicle/Vehicle_{body.prefix.title()}_01.MON"
            return mon_rows(self.read(filename), "Vehicle", filename)
        if kind in ("wing", "pet"):
            folder = f"Character/{kind.title()}"
            filename = f"{folder}/{kind.title()}.MON"
            return mon_rows(self.read(filename), folder, filename) if self.resolve(filename) else {}
        return {}

    def weapon_choices(self, family: int) -> dict[int, ModelChoice]:
        filename = f"Item/{family:02d}.ITM"
        if self.resolve(filename) is None:
            filename = f"Item/{WEAPON_TYPES[family]:02d}.ITM"
        rows = table_rows(self.read(filename), "weapon")
        return {
            index: ModelChoice(mesh, (Surface(f"Item/3DO/{mesh}", f"Item/DDS/{dds_name(texture)}", "rigid", cut),),
                               glow, filename)
            for index, row in enumerate(rows[:256]) if row is not None
            for mesh, texture, cut, glow in (row,) if texture
        }

    def armor_choices(self, family: int, body: Body, female: bool) -> dict[int, ModelChoice]:
        folder = f"Character/{body.race}"
        prefix = body.archetype(female)
        part = ARMOR_TYPES[family]
        filename = f"{folder}/{prefix}_{part}.MLT"
        rows = table_rows(self.read(filename), "armor")
        return {
            index: armor_choice(mesh, texture, cut, folder, f"{folder}/{armor_pose(prefix)}", filename)
            for index, row in enumerate(rows[:256]) if row is not None
            for mesh, texture, cut, _ in (row,)
        }

    def cape_choices(self, body: Body, female: bool, fit: int) -> dict[int, ModelChoice]:
        folder = f"cloak/Character/{body.race}"
        prefix = body.archetype(female)
        texture_folder = "cloak/ClothTexture"
        suffix = body.prefix.upper()
        textures = Cursor(self.read(f"{texture_folder}/CLOTH_TEXTHRE_{suffix}.CTL"))
        names = tuple(decode_text(textures.take(256)) for _ in range(textures.count(256)))
        textures.finish()
        filename = f"{folder}/{fit}_{prefix}.PC"
        rows = cape_rows(self.read(filename))
        pose = f"Character/{body.race}/{armor_pose(prefix)}"
        choices = {}
        for index, row in enumerate(rows[:256]):
            if row is None:
                continue
            texture = entry(names, row.texture)
            if not texture:
                continue
            path = f"{texture_folder}/{suffix}/{texture}"
            surfaces = [Surface(
                f"{folder}/3DC/{row.mesh}", path, "cloth", True, True, pose, row.pins, row.grid,
            )]
            if row.shoulder:
                surfaces.append(Surface(f"{folder}/3DC/Static/{row.shoulder}", path, "skin", False, True, pose))
            choices[index] = ModelChoice(texture, tuple(surfaces), table=filename)
        return choices
