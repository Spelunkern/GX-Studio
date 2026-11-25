from dataclasses import dataclass
from pathlib import Path
from struct import Struct

from . import seed
from .fields import FIELDS_BY_NAME, Field

WORD = Struct("<I")
BODY_SIZE = 81
MAX_BYTES = 16 * 1024 * 1024
Scalar = str | int
Edits = dict[int, dict[str, Scalar]]


def decode_text(raw: bytes) -> str:
    text = raw.split(b"\0", 1)[0]
    try:
        return text.decode("utf-8")
    except UnicodeDecodeError:
        return text.decode("cp1252", errors="replace")


@dataclass(frozen=True, slots=True)
class Item:
    item_id: int
    family: int
    type_id: int
    start: int
    description: int
    body: int

    @property
    def end(self) -> int:
        return self.body + BODY_SIZE


class Cursor:
    def __init__(self, data: bytes, filename: str = "Item.SData"):
        self.data = data
        self.filename = filename
        self.position = 0

    def take(self, size: int) -> bytes:
        end = self.position + size
        if end > len(self.data):
            raise ValueError(f"{self.filename} is truncated at byte {self.position}.")
        result = self.data[self.position:end]
        self.position = end
        return result

    def word(self) -> int:
        return WORD.unpack(self.take(WORD.size))[0]

    def string(self) -> bytes:
        return self.take(self.word())


def read_items(plain: bytes) -> dict[int, Item]:
    cursor = Cursor(plain)
    groups = cursor.word()
    if not 1 <= groups <= 255:
        raise ValueError("The family count does not match the GX EP5 format.")
    items = {}
    for family in range(1, groups + 1):
        count = cursor.word()
        if count > 255:
            raise ValueError(f"Family {family} exceeds the EP5 limit of 255 records.")
        for _ in range(count):
            start = cursor.position
            name = cursor.string()
            description = cursor.position
            details = cursor.string()
            body = cursor.position
            record = cursor.take(BODY_SIZE)
            if not any(record) and not decode_text(name) and not decode_text(details):
                continue
            item_type, type_id = record[:2]
            item_id = item_type * 1000 + type_id
            if item_type != family or not type_id or item_id in items:
                raise ValueError(f"Invalid or duplicate identity: {item_type}/{type_id} in family {family}.")
            items[item_id] = Item(item_id, family, type_id, start, description, body)
    if cursor.position != len(plain):
        raise ValueError(f"The catalog contains {len(plain) - cursor.position} trailing bytes.")
    return items


def read_file(path: Path) -> bytes:
    with path.open("rb") as file:
        raw = file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("The catalog exceeds the GX limit of 16 MiB.")
    return raw


class Catalog:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.plain, self.writable = seed.decode(raw)
        self.encrypted = raw.startswith(seed.SIGNATURE)
        self.items = read_items(self.plain)

    @classmethod
    def open(cls, path: Path) -> "Catalog":
        return cls(read_file(path))

    def value(self, item: Item, field: Field) -> Scalar:
        if field.size:
            start = item.body + field.offset
            if field.size == 1:
                return self.plain[start]
            return int.from_bytes(self.plain[start:start + field.size], "little")
        start, stop = self._text_positions(item, field)
        return decode_text(self.plain[start + WORD.size:stop])

    @staticmethod
    def _text_positions(item: Item, field: Field) -> tuple[int, int]:
        if field.offset == 0:
            return item.start, item.description
        return item.description, item.body

    def _patch_item(self, item: Item, values: dict[str, Scalar]) -> bytes:
        body = bytearray(self.plain[item.body:item.end])
        strings = [self.plain[item.start:item.description], self.plain[item.description:item.body]]
        for name, value in values.items():
            field = FIELDS_BY_NAME[name]
            parsed = field.parse(str(value))
            if parsed == self.value(item, field):
                continue
            if isinstance(parsed, int):
                body[field.offset:field.offset + field.size] = parsed.to_bytes(field.size, "little")
            else:
                index = field.offset
                _, nul, suffix = strings[index][WORD.size:].partition(b"\0")
                data = parsed.encode("utf-8") + nul + suffix
                strings[index] = WORD.pack(len(data)) + data
        return b"".join((*strings, body))

    def encode(self, edits: Edits) -> bytes:
        if not edits:
            return self.raw
        if not self.writable:
            raise ValueError("This encrypted file is read-only.")
        output = bytearray()
        offset = 0
        for item_id in sorted(edits, key=lambda key: self.items[key].start):
            item = self.items[item_id]
            output.extend(self.plain[offset:item.start])
            output.extend(self._patch_item(item, edits[item_id]))
            offset = item.end
        output.extend(self.plain[offset:])
        if output == self.plain:
            return self.raw
        if len(output) > MAX_BYTES:
            raise ValueError("The edited catalog exceeds the GX limit of 16 MiB.")
        raw = seed.encode_like(self.raw, bytes(output))
        if len(raw) > MAX_BYTES:
            raise ValueError("The encrypted catalog exceeds the GX limit of 16 MiB.")
        verified = Catalog(raw)
        if verified.plain != output or verified.items.keys() != self.items.keys():
            raise ValueError("Could not verify the generated catalog.")
        return raw
