"""The original server map layout read by GX World's svmap loader."""

from dataclasses import dataclass
from pathlib import Path
from struct import Struct

from .catalog import Cursor, decode_text

I32 = Struct("<i")
POINT = Struct("<3f")
BOUNDS = Struct("<6f")
MONSTER = Struct("<II")
PATROL = Struct("<4f")
PORTAL = Struct("<3fiHHI3f")
SPAWN = Struct("<Iii")
NAMED = Struct("<6fii")
MAX_FILE_BYTES = 32 << 20
MAX_MAP_SIZE = 8192


@dataclass(frozen=True, slots=True)
class MonsterArea:
    bounds: tuple
    spawns: tuple


@dataclass(frozen=True, slots=True)
class NPC:
    kind: int
    identifier: int
    patrol: tuple


@dataclass(frozen=True, slots=True)
class SpawnArea:
    faction: int
    auxiliary: int
    boxes: tuple


@dataclass(frozen=True, slots=True)
class ServerMap:
    size: int
    mask: bytes
    cell_size: int
    ladders: tuple
    monsters: tuple
    npcs: tuple
    portals: tuple
    spawns: tuple
    named: tuple
    tail: bytes
    raw: bytes

    @property
    def trailing_bytes(self):
        return len(self.tail)

    def encode(self):
        output = bytearray(I32.pack(self.size))
        output.extend(self.mask)
        output.extend(I32.pack(self.cell_size))

        def count(entries):
            output.extend(I32.pack(len(entries)))

        def rows(codec, entries):
            count(entries)
            for entry in entries:
                output.extend(codec.pack(*entry))

        rows(POINT, self.ladders)
        count(self.monsters)
        for area in self.monsters:
            output.extend(BOUNDS.pack(*area.bounds))
            rows(MONSTER, area.spawns)
        count(self.npcs)
        for npc in self.npcs:
            output.extend(I32.pack(npc.kind))
            output.extend(I32.pack(npc.identifier))
            rows(PATROL, npc.patrol)
        rows(PORTAL, self.portals)
        count(self.spawns)
        for group in self.spawns:
            output.extend(SPAWN.pack(len(group.boxes), group.faction, group.auxiliary))
            for box in group.boxes:
                output.extend(BOUNDS.pack(*box))
        rows(NAMED, self.named)
        output.extend(self.tail)
        if len(output) > MAX_FILE_BYTES:
            raise ValueError("The server map exceeds 32 MiB.")
        return bytes(output)

    def blocked(self, x, z):
        if not (0 <= x < self.size and 0 <= z < self.size):
            return True
        bit = z * self.size + x
        return bit // 8 >= len(self.mask) or bool(self.mask[bit // 8] & (1 << (bit % 8)))

    @classmethod
    def read(cls, raw, filename=".svmap"):
        cursor = Cursor(raw, filename)

        def read(codec):
            return codec.unpack(cursor.take(codec.size))

        def count(label, minimum):
            value = read(I32)[0]
            if not 0 <= value <= (len(raw) - cursor.position) // minimum:
                raise ValueError(f"Invalid {label} count in {filename}: {value}.")
            return value

        def rows(codec, length):
            return tuple(codec.iter_unpack(cursor.take(codec.size * length)))

        size = read(I32)[0]
        if not 0 < size <= MAX_MAP_SIZE:
            raise ValueError(f"Invalid map size in {filename}: {size}.")
        mask = cursor.take(size * size // 8)
        cell_size = read(I32)[0]
        if cell_size < 0:
            raise ValueError(f"Invalid cell size in {filename}: {cell_size}.")
        ladders = rows(POINT, count("ladder", POINT.size))
        monsters = []
        for _ in range(count("monster area", BOUNDS.size + I32.size)):
            bounds = read(BOUNDS)
            monsters.append(MonsterArea(bounds, rows(MONSTER, count("monster spawn", MONSTER.size))))
        npcs = []
        for _ in range(count("NPC", 3 * I32.size)):
            kind, identifier = read(I32)[0], read(I32)[0]
            npcs.append(NPC(kind, identifier, rows(PATROL, count("NPC patrol point", PATROL.size))))
        portals = rows(PORTAL, count("portal", PORTAL.size))
        spawns = []
        for _ in range(count("spawn area", SPAWN.size)):
            boxes = count("spawn box", BOUNDS.size)
            faction, auxiliary = read(I32)[0], read(I32)[0]
            spawns.append(SpawnArea(faction, auxiliary, rows(BOUNDS, boxes)))
        named = rows(NAMED, count("named area", NAMED.size))
        return cls(size, mask, cell_size, ladders, tuple(monsters), tuple(npcs), portals,
                   tuple(spawns), named, raw[cursor.position:], raw)

    @classmethod
    def open(cls, path: Path):
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("The server map exceeds 32 MiB.")
        return cls.read(raw, path.name)


@dataclass(frozen=True, slots=True)
class AreaName:
    bounds: tuple
    label: str
    source: str


def read_world_area_names(raw, localized, map_id, banner_file):
    cursor = Cursor(raw, f"world/{map_id}.wld")

    def skip(count, size):
        cursor.take(count * size)

    def skip_list(size):
        skip(cursor.word(), size)

    def group():
        skip_list(256)
        skip_list(40)

    kind = cursor.take(4).rstrip(b"\0")
    if kind == b"FLD":
        size = cursor.word()
        if not 0 < size <= MAX_MAP_SIZE:
            raise ValueError("Invalid WLD map size.")
        skip((size // 2 + 1) ** 2, 3)
        skip_list(516)
    elif kind != b"DUN":
        raise ValueError("Unknown WLD map format.")
    cursor.take(256)
    for _ in range(7):
        group()
    skip_list(256)
    skip_list(44)
    cursor.take(256)
    skip_list(40)
    cursor.take(12)
    group()
    skip_list(256)
    skip_list(36)
    skip_list(256)
    for _ in range(cursor.word()):
        cursor.take(24)
        skip_list(4)
    for size in (20, 28, 556, 40):
        skip_list(size)
    areas = []
    record_size = BOUNDS.size + 4 + 512 + 8
    count = cursor.word()
    if count > (len(raw) - cursor.position) // record_size:
        raise ValueError("Invalid WLD named area count.")
    for index in range(count):
        bounds = BOUNDS.unpack(cursor.take(BOUNDS.size))
        cursor.take(4)
        text = decode_text(cursor.take(256)).strip()
        comment = decode_text(cursor.take(256)).strip()
        mode = I32.unpack(cursor.take(4))[0]
        cursor.take(4)
        if mode == 2 and text:
            stem = Path(text.replace("\\", "/")).stem
            label, source = localized.get(index) or stem, banner_file(stem)
        elif mode == 0 and index in localized:
            label, source = localized[index], f"world/{map_id}.txt · #{index}"
        else:
            label, source = comment or text, f"world/{map_id}.wld · #{index}"
        areas.append(AreaName(bounds, label, source))
    return tuple(areas)


def read_area_text(raw):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16", errors="replace")
    elif b"\0" in raw:
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    labels = {}
    for line in text.splitlines():
        key, separator, value = line.partition("\t")
        if separator and key.strip().isascii() and key.strip().isdecimal():
            labels[int(key)] = value.strip()
    return labels
