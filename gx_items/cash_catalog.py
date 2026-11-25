from dataclasses import dataclass, replace
from pathlib import Path
from struct import Struct

from . import seed
from .catalog import Cursor, MAX_BYTES, WORD, decode_text, read_file

BAGS = ("Hidden", "EXP & More", "Buffs", "Character & Fun", "Gear & Upgrades")
SLOTS = 24
HEADER = Struct("<4I")
SLOT = Struct("<IB")
EMPTY_BUNDLE = ((0, 0),) * SLOTS


@dataclass(frozen=True, slots=True)
class Product:
    index: int
    bag: int
    icon: int
    cost: int
    bundle: tuple[tuple[int, int], ...]
    name: str
    code: str
    description: str


def validate_products(products: tuple[Product, ...], *, for_database: bool = False):
    codes = set()
    for product in products:
        code = product.code
        if not 1 <= len(code) <= 20 or any(not 33 <= ord(ch) <= 126 for ch in code):
            raise ValueError(f"Product code {code!r} must use 1–20 printable ASCII characters without spaces.")
        if code.lower() in codes:
            raise ValueError(f"Duplicate product code {code!r}.")
        codes.add(code.lower())
        if not 0 <= product.bag < len(BAGS) or any(not 0 <= value <= 0xffffffff for value in
                                                   (product.index, product.icon, product.cost)):
            raise ValueError(f"Product {code} has an invalid category, icon, index or cost.")
        for value in (product.name, product.description):
            if "\0" in value:
                raise ValueError(f"Product {code} contains a NUL character.")
            value.encode("utf-8")
        if len(product.bundle) != SLOTS:
            raise ValueError(f"Product {code} must have {SLOTS} slots.")
        for slot, (item_id, count) in enumerate(product.bundle, 1):
            family, type_id = divmod(item_id, 1000)
            if (item_id or count) and not (1 <= family <= 255 and 1 <= type_id <= 255 and 1 <= count <= 255):
                raise ValueError(f"Product {code}, slot {slot}: enter a valid item ID with a quantity from 1 to 255.")
        if (for_database or product.cost) and product.bundle == EMPTY_BUNDLE:
            raise ValueError(f"Product {code} needs at least one item in its bundle.")


class CashCatalog:
    def __init__(self, raw: bytes):
        self.raw = raw
        plain, self.writable = seed.decode(raw)
        cursor = Cursor(plain)
        count = cursor.word()
        if count > (len(plain) - 4) // (HEADER.size + SLOTS * SLOT.size + 12):
            raise ValueError("Invalid product count in Cash.SData.")
        self.strings: dict[int, tuple[bytes, ...]] = {}
        products = []
        for row in range(count):
            header = HEADER.unpack(cursor.take(HEADER.size))
            bundle = tuple(SLOT.iter_unpack(cursor.take(SLOTS * SLOT.size)))
            strings = self.strings[row] = tuple(cursor.string() for _ in range(3))
            products.append(Product(*header, bundle, *(decode_text(value) for value in strings)))
        if cursor.position != len(plain):
            raise ValueError("Unexpected bytes after the Cash.SData catalog.")
        self.products = tuple(products)

    @classmethod
    def open(cls, path: Path):
        return cls(read_file(path))

    def encode(self, rows: dict[int, Product]) -> bytes:
        products = tuple(rows.values())
        if products == self.products:
            return self.raw
        if not self.writable:
            raise ValueError("This encrypted file is read-only.")
        validate_products(products)
        reorder = tuple(rows) != tuple(range(len(self.products)))
        output = bytearray(WORD.pack(len(products)))
        for new_index, (row, product) in enumerate(rows.items()):
            original = self.products[row] if row in self.strings else None
            # Native indices follow row order when products are added or removed.
            index = new_index if reorder else product.index
            output.extend(HEADER.pack(index, product.bag, product.icon, product.cost))
            output.extend(b"".join(SLOT.pack(*slot) for slot in product.bundle))
            for i, field in enumerate(("name", "code", "description")):
                value = getattr(product, field)
                if original is not None and value == getattr(original, field):
                    raw = self.strings[row][i]
                else:
                    original_text = self.strings[row][i] if original is not None else b""
                    _, nul, suffix = original_text.partition(b"\0")
                    raw = value.encode("utf-8") + nul + suffix
                output.extend(WORD.pack(len(raw)) + raw)
        encoded = seed.encode_like(self.raw, bytes(output))
        if len(encoded) > MAX_BYTES:
            raise ValueError("The Cash.SData catalog exceeds 16 MiB.")
        expected = (tuple(replace(product, index=index) for index, product in enumerate(products))
                    if reorder else products)
        if CashCatalog(encoded).products != expected:
            raise ValueError("Cash.SData failed round-trip verification.")
        return encoded
