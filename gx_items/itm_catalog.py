"""Native ITM and IT2 equipment model tables."""

from dataclasses import dataclass
from pathlib import Path
from struct import Struct

from .catalog import Cursor, WORD, decode_text

ROW = Struct("<6i")
EXTENDED = Struct("<Iffi")
BINDINGS_SIZE = 16 * 2 * 32
EMPTY_BINDINGS = bytes(BINDINGS_SIZE)
DEFAULT_EXTENDED = EXTENDED.pack(0xFFFFFFFF, 0, 1, 0)
SIGNATURES = (b"ITM", b"IT2")
MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ItmRecord:
    mesh: int = -1
    texture: int = -1
    glow: int = -1
    parameter: int = 0
    variant: int = 0
    parameter2: int = 0
    extended: bytes = b""
    bindings: bytes = b""

    @property
    def cutout(self):
        return self.alpha_flag == 0

    @property
    def alpha_flag(self):
        return EXTENDED.unpack(self.extended)[-1] if self.variant else self.parameter2


def name_at(names: tuple[bytes, ...], index: int) -> str:
    return decode_text(names[index]) if 0 <= index < len(names) else ""


class ItmCatalog:
    def __init__(self, raw: bytes):
        if len(raw) > MAX_BYTES:
            raise ValueError("The ITM file exceeds 32 MiB.")
        self.raw = raw
        reader = Cursor(raw, "ITM")
        self.signature = reader.take(3)
        if self.signature not in SIGNATURES:
            raise ValueError("Expected an ITM or IT2 model table.")
        self.meshes, self.textures = (
            tuple(reader.string() for _ in range(self.count(reader, 4))) for _ in range(2)
        )
        binding_size = BINDINGS_SIZE if self.signature == b"IT2" else 0
        records = []
        for _ in range(self.count(reader, ROW.size + binding_size)):
            values = ROW.unpack(reader.take(ROW.size))
            variant = values[4]
            if variant not in (0, 1):
                raise ValueError(f"Unsupported ITM model record format {variant}.")
            extended = reader.take(EXTENDED.size) if variant else b""
            records.append(ItmRecord(*values, extended, reader.take(binding_size)))
        if reader.position != len(raw):
            raise ValueError(f"Unexpected bytes after the ITM records at byte {reader.position}.")
        self.records = tuple(records)

    @staticmethod
    def count(reader, stride):
        count = reader.word()
        if count > (len(reader.data) - reader.position) // stride:
            raise ValueError(f"Invalid ITM count at byte {reader.position}.")
        return count

    @staticmethod
    def read(path: Path) -> bytes:
        with path.open("rb") as stream:
            return stream.read(MAX_BYTES + 1)

    @classmethod
    def open(cls, path: Path):
        return cls(cls.read(path))

    def encode(self, meshes, textures, records):
        records, meshes, textures = tuple(records), tuple(meshes), tuple(textures)
        if (meshes, textures, records) == (self.meshes, self.textures, self.records):
            return self.raw
        output = bytearray(self.signature)
        for names in (meshes, textures):
            output.extend(WORD.pack(len(names)))
            for name in names:
                output.extend(WORD.pack(len(name)))
                output.extend(name)
        output.extend(WORD.pack(len(records)))
        for row in records:
            output.extend(ROW.pack(row.mesh, row.texture, row.glow, row.parameter, row.variant, row.parameter2))
            output.extend(row.extended)
            output.extend(row.bindings)
        raw = bytes(output)
        check = ItmCatalog(raw)
        if (check.meshes, check.textures, check.records) != (meshes, textures, records):
            raise ValueError("ITM failed round-trip verification.")
        return raw
