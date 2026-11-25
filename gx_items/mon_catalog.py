from dataclasses import dataclass, replace
from math import isfinite
from pathlib import Path
from struct import Struct, error as StructError

from .catalog import Cursor, WORD, decode_text

HEIGHT = Struct("<f")
BONE_EFFECT = Struct("<ii")
ABSENT = b"LOAD"
FORMATS = (b"MO2", b"MO4")
MAX_BYTES = 32 * 1024 * 1024
ANIMATIONS = ("Walk", "Run", "Jump/Atk", "Attack 2", "Attack 3", "Die", "Breath", "Damage", "Idle")
WAVES = ("Attack 1", "Attack 2", "Attack 3", "Die")
EFFECTS = (*WAVES, "Attach")


def slot_text(raw: bytes) -> str:
    text = decode_text(raw)
    return "" if text.casefold() == "load" else text


def text_bytes(text: str, before: bytes, *, slot=False) -> bytes:
    if text == (slot_text(before) if slot else decode_text(before)):
        return before
    if "\0" in text:
        raise ValueError("Text cannot contain a NUL character.")
    return ABSENT if slot and not text else text.encode("utf-8")


@dataclass(frozen=True, slots=True)
class MonRecord:
    name: bytes = b""
    flags: int = 255
    animations: tuple[bytes, ...] = (ABSENT,) * 9
    waves: tuple[bytes, ...] = (ABSENT,) * 4
    effects: tuple[bytes, ...] = (ABSENT,) * 5
    parts: tuple[tuple[bytes, bytes], ...] = ()
    height_bytes: bytes = bytes(4)
    bone_effects: tuple[tuple[int, int], ...] = ()

    @property
    def display_name(self):
        return decode_text(self.name)

    @property
    def height(self):
        return HEIGHT.unpack(self.height_bytes)[0]

    def edit_height(self, value: str) -> bytes:
        if value.strip() == str(self.height):
            return self.height_bytes
        try:
            height = float(value or "0")
            if isfinite(height):
                return HEIGHT.pack(height)
        except (ValueError, OverflowError, StructError):
            pass
        raise ValueError("Height must be a finite 32-bit number.")


class MonCatalog:
    def __init__(self, raw: bytes):
        if len(raw) > MAX_BYTES:
            raise ValueError("The MON file exceeds 32 MiB.")
        self.raw = raw
        reader = Cursor(raw, "MON")
        self.signature = reader.take(3)
        if self.signature not in FORMATS:
            raise ValueError("Expected a MO2 or MO4 model table.")
        records = []
        count = self.count(reader, 85)
        for _ in range(count):
            name = reader.string()
            flags = reader.take(1)[0]
            animations = tuple(reader.string() for _ in ANIMATIONS)
            waves = tuple(reader.string() for _ in WAVES)
            effects = tuple(reader.string() for _ in range(5 if self.signature == b"MO4" else 4))
            if self.signature == b"MO2":
                effects += (ABSENT,)
            parts = tuple((reader.string(), reader.string()) for _ in range(self.count(reader, 8)))
            height = reader.take(HEIGHT.size)
            bones = tuple(BONE_EFFECT.unpack(reader.take(BONE_EFFECT.size)) for _ in range(self.count(reader, 8)))
            records.append(MonRecord(name, flags, animations, waves, effects, parts, height, bones))
        if reader.position != len(raw):
            raise ValueError(f"Unexpected bytes after the MON records at byte {reader.position}.")
        self.records = tuple(records)

    @staticmethod
    def count(reader, stride):
        count = reader.word()
        if count > (len(reader.data) - reader.position) // stride:
            raise ValueError(f"Invalid MON record count at byte {reader.position}.")
        return count

    @classmethod
    def open(cls, path: Path):
        return cls(read_mon(path))

    def encode(self, records, signature):
        records = tuple(records)
        if signature not in FORMATS:
            raise ValueError("Expected a MO2 or MO4 model table.")
        if signature == self.signature and records == self.records:
            return self.raw
        output = bytearray(signature + WORD.pack(len(records)))

        def string(value):
            output.extend(WORD.pack(len(value)))
            output.extend(value)

        for record in records:
            if tuple(map(len, (record.animations, record.waves, record.effects))) != (9, 4, 5):
                raise ValueError("The MON record has invalid animation, wave or effect slots.")
            string(record.name)
            output.append(record.flags)
            for value in (*record.animations, *record.waves, *record.effects[:5 if signature == b"MO4" else 4]):
                string(value)
            output.extend(WORD.pack(len(record.parts)))
            for mesh, texture in record.parts:
                string(mesh)
                string(texture)
            output.extend(record.height_bytes)
            output.extend(WORD.pack(len(record.bone_effects)))
            for effect in record.bone_effects:
                output.extend(BONE_EFFECT.pack(*effect))
        raw = bytes(output)
        expected = records if signature == b"MO4" else tuple(replace(record, effects=(*record.effects[:4], ABSENT))
                                                               for record in records)
        if MonCatalog(raw).records != expected:
            raise ValueError("MON failed round-trip verification.")
        return raw


def read_mon(path: Path):
    with path.open("rb") as stream:
        return stream.read(MAX_BYTES + 1)


def save_mon(path: Path, catalog: MonCatalog, records, signature, backups: Path):
    from .install import save_model_table

    return save_model_table(path, catalog, catalog.encode(records, signature), backups,
                            format_name="MON", read=read_mon)
