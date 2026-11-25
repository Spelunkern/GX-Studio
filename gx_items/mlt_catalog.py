"""Native indexed MLT mesh and material tables."""

from dataclasses import dataclass
from pathlib import Path
from struct import Struct

from .catalog import Cursor, WORD

RECORD = Struct("<3i")
MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MltRecord:
    mesh: int = -1
    texture: int = -1
    alpha: int = 0


class MltCatalog:
    def __init__(self, raw: bytes):
        if len(raw) > MAX_BYTES:
            raise ValueError("The MLT file exceeds 32 MiB.")
        self.raw = raw
        reader = Cursor(raw, "MLT")
        self.signature = reader.take(3)
        # GX accepts both signatures in files with the .MLT extension.
        if self.signature not in (b"MLT", b"ML2"):
            raise ValueError("Expected a native MLT model table.")
        self.meshes, self.textures = (
            tuple(reader.string() for _ in range(self.count(reader, 4))) for _ in range(2)
        )
        self.records = tuple(
            MltRecord(*RECORD.unpack(reader.take(RECORD.size))) for _ in range(self.count(reader, RECORD.size))
        )
        self.trailing = raw[reader.position:]

    @staticmethod
    def count(reader, stride):
        count = reader.word()
        if count > (len(reader.data) - reader.position) // stride:
            raise ValueError(f"Invalid MLT count at byte {reader.position}.")
        return count

    @staticmethod
    def read(path: Path) -> bytes:
        with path.open("rb") as stream:
            return stream.read(MAX_BYTES + 1)

    @classmethod
    def open(cls, path: Path):
        return cls(cls.read(path))

    def encode(self, meshes, textures, records):
        meshes, textures, records = tuple(meshes), tuple(textures), tuple(records)
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
            output.extend(RECORD.pack(row.mesh, row.texture, row.alpha))
        output.extend(self.trailing)
        raw = bytes(output)
        check = MltCatalog(raw)
        if (check.meshes, check.textures, check.records, check.trailing) != (meshes, textures, records, self.trailing):
            raise ValueError("MLT failed round-trip verification.")
        return raw
