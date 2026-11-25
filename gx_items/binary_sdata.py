"""Read EP8 client tables with the BinarySData layouts documented by Parsec."""

from pathlib import Path
import re
from struct import Struct

from . import seed
from .catalog import Cursor, decode_text

INT64 = Struct("<q")
MAX_BYTES = 128 << 20
# Column count, numeric prefix length, and string length prefix width.
# Numeric-only tables use the same 64-bit cell layout for every column.
SCHEMAS = {
    "dbitemdata": (70, 70, 0), "dbitemtext": (4, 2, 4),
    "dbskilldata": (101, 101, 0), "dbskilltext": (4, 2, 4),
    "dbnpcskilldata": (100, 100, 0), "dbnpcskilltext": (4, 2, 4),
    "dbmonsterdata": (94, 94, 0), "dbmonstertext": (2, 1, 4),
    "dbitemselldata": (54, 0, 1), "dbitemselltext": (3, 1, 4),
    "dbsetitemdata": (27, 27, 0), "dbsetitemtext": (15, 1, 4),
    "dbduallayerclothesdata": (7, 7, 0),
    "dbtransformmodeldata": (7, 7, 0), "dbtransformweaponmodeldata": (3, 3, 0),
}


def binary_schema(name):
    stem, extension = Path(name).stem.casefold(), Path(name).suffix.casefold()
    if extension != ".sdata":
        return None
    return SCHEMAS.get(re.sub(r"_[a-z]{2,5}$", "", stem))


class BinarySData:
    def __init__(self, name, raw):
        schema = binary_schema(name)
        if schema is None:
            raise ValueError(f"{name} is not a recognized EP8 client table.")
        self.plain, _ = seed.decode(raw, binary=True)
        cursor = Cursor(self.plain, name)
        cursor.take(128)
        columns, prefix, text_width = schema
        if cursor.word() != columns:
            raise ValueError(f"{name} does not have the expected {columns} columns.")
        self.headers = tuple(cursor.take(2 * cursor.take(1)[0]).decode("utf-16-le") for _ in range(columns))
        self.count = cursor.word()
        self.start = cursor.position
        self.stride = columns * INT64.size
        self.rows = ()
        if not text_width:
            cursor.position += self.count * self.stride
            if cursor.position > len(self.plain):
                raise ValueError(f"Invalid row count in {name}.")
        else:
            minimum = prefix * INT64.size + (1 + (columns - 1) * INT64.size if text_width == 1
                                              else (columns - prefix) * 4)
            if self.count > (len(self.plain) - self.start) // minimum:
                raise ValueError(f"Invalid row count in {name}.")
            rows = []
            for _ in range(self.count):
                if text_width == 1:
                    code = decode_text(cursor.take(cursor.take(1)[0]))
                    cells = tuple(value for value, in INT64.iter_unpack(cursor.take((columns - 1) * INT64.size)))
                    rows.append((code, *cells))
                else:
                    cells = tuple(value for value, in INT64.iter_unpack(cursor.take(prefix * INT64.size)))
                    rows.append((*cells, *(decode_text(cursor.string()) for _ in range(columns - prefix))))
            self.rows = tuple(rows)
        padding = self.plain[cursor.position:]
        if len(padding) > 15 or any(padding):
            raise ValueError(f"Unexpected bytes after the rows of {name}.")

    def value(self, row, column):
        if self.rows:
            return self.rows[row][column]
        return INT64.unpack_from(self.plain, self.start + row * self.stride + column * INT64.size)[0]

    @classmethod
    def open(cls, path):
        with path.open("rb") as file:
            raw = file.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError(f"{path.name} exceeds the EP8 table size limit of 128 MiB.")
        return cls(path.name, raw)
