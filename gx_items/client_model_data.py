"""Editable ALT action tables and cloak PC/CTL data with native byte preservation."""

from dataclasses import dataclass
from struct import Struct

from .catalog import WORD, decode_text, read_file
from .equipment_models import Cursor, PC_PIN, PC_ROW
from .mon_catalog import text_bytes

ACTION = Struct("<II4f")
PC_SIZE = 524
PIN_OFFSET = 44
EMBLEM = Struct("<4I")
EMBLEM_TABLES = {
    "emblemfront.dat": "BackgroundTex",
    "emblemback.dat": "BackgroundTex",
    "emblemlist.dat": "EmblemTex",
}
EMBLEM_FILTER = "EmblemFront*.dat EmblemBack*.dat EmblemList*.dat emblemfront*.dat emblemback*.dat emblemlist*.dat *.DAT"


def emblem_texture_directory(path):
    if path.suffix.casefold() == ".dat":
        return EMBLEM_TABLES.get(path.stem.casefold().split("-", 1)[0] + ".dat")
    return None


class ClientModelCatalog:
    read = staticmethod(read_file)

    @classmethod
    def open(cls, path):
        return cls(cls.read(path))

    @staticmethod
    def verified(raw, expected, catalog_type):
        if catalog_type(raw).records != expected:
            raise ValueError("The generated file failed round-trip verification.")
        return raw


@dataclass(frozen=True, slots=True)
class AltRecord:
    name: bytes = b""
    parameters: bytes = b""

    def values(self):
        return ACTION.unpack(self.parameters) if self.parameters else (0, 0, 0., 0., 0., 0.)


class AltCatalog(ClientModelCatalog):
    def __init__(self, raw):
        self.raw = raw
        reader = Cursor(raw)
        if reader.take(3) != b"AL3":
            raise ValueError("Expected an AL3 animation table.")
        records = []
        for _ in range(reader.count(4)):
            name = reader.take(reader.count(1))
            records.append(AltRecord(name, reader.take(ACTION.size) if decode_text(name) else b""))
        reader.finish()
        self.records = tuple(records)

    def encode(self, records):
        records = tuple(records)
        if records == self.records:
            return self.raw
        data = b"AL3" + WORD.pack(len(records))
        data += b"".join(WORD.pack(len(row.name)) + row.name + row.parameters for row in records)
        return self.verified(data, records, AltCatalog)


class TextureCatalog(ClientModelCatalog):
    record_size = 256
    max_records = None
    padding = b"\0"

    def __init__(self, raw):
        self.raw = raw
        reader = Cursor(raw)
        count = reader.count(self.record_size)
        if self.max_records is not None and count > self.max_records:
            raise ValueError(f"This table supports at most {self.max_records} entries.")
        self.records = tuple(reader.take(self.record_size) for _ in range(count))
        reader.finish()

    def encode(self, records):
        records = tuple(records)
        return self.verified(WORD.pack(len(records)) + b"".join(records), records, type(self))

    @classmethod
    def name(cls, text, original):
        encoded = text_bytes(text, original)
        if encoded is original:
            return original
        if len(encoded) >= cls.record_size:
            raise ValueError(f"The texture name accepts up to {cls.record_size - 1} UTF-8 bytes.")
        return (encoded + b"\0").ljust(cls.record_size, cls.padding)


class EmblemCatalog(TextureCatalog):
    record_size = 260
    max_records = 255
    padding = b"\xcc"


class CloakCatalog(ClientModelCatalog):
    def __init__(self, raw):
        self.raw = raw
        reader = Cursor(raw)
        self.bodies = self.read_names(reader)
        self.records = tuple(reader.take(PC_SIZE) for _ in range(reader.count(PC_SIZE)))
        self.shoulders = self.read_names(reader)
        reader.finish()

    @staticmethod
    def read_names(reader):
        entries = tuple(reader.take(132) for _ in range(reader.count(132)))
        if any(int.from_bytes(entry[:4], "little") >= len(entries) for entry in entries):
            raise ValueError("Invalid cloak mesh index.")
        return entries

    @staticmethod
    def names(entries):
        names = [""] * len(entries)
        for entry in entries:
            names[int.from_bytes(entry[:4], "little")] = decode_text(entry[4:])
        return tuple(names)

    @staticmethod
    def reference(entries, index, text):
        names = CloakCatalog.names(entries)
        if 0 <= index < len(names) and names[index] == text:
            return entries, index
        if text in names:
            return entries, names.index(text)
        encoded = text_bytes(text, b"")
        if not encoded or len(encoded) > 127:
            raise ValueError("A cloak mesh name must contain 1–127 UTF-8 bytes.")
        return (*entries, WORD.pack(len(entries)) + encoded.ljust(128, b"\0")), len(entries)

    def encode(self, records, bodies=None, shoulders=None):
        records = tuple(records)
        bodies = self.bodies if bodies is None else bodies
        shoulders = self.shoulders if shoulders is None else shoulders
        raw = b"".join(WORD.pack(len(table)) + b"".join(table) for table in (bodies, records, shoulders))
        checked = CloakCatalog(raw)
        if (checked.bodies, checked.records, checked.shoulders) != (bodies, records, shoulders):
            raise ValueError("The cloak file failed round-trip verification.")
        return raw


def cloak_values(row):
    return PC_ROW.unpack_from(row)


def cloak_pins(row):
    return tuple(PC_PIN.unpack_from(row, PIN_OFFSET + number * PC_PIN.size) for number in range(20))


def cloak_emblem(row):
    return EMBLEM.unpack_from(row, PC_ROW.size)
