import json
import os
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from errno import EBADF, EINVAL, ENOTSUP
from hashlib import sha256
from pathlib import Path
from sqlite3 import Connection, connect
from stat import S_IMODE
from tempfile import NamedTemporaryFile, mkdtemp

from .cash_catalog import SLOTS, Product
from .catalog import Catalog, Scalar, read_file
from .fields import FIELDS
from .skill_catalog import SKILL_SQL_FIELDS
from .workspace import COLUMNS, NPC_SKILL_TABLE, SKILL_TABLE, TABLE, assert_safe_table, quote, read_database

PRODUCT_TABLE = "PS_GameDefs_ProductList"
PRODUCT_SLOTS = tuple(column for slot in range(1, SLOTS + 1) for column in (f"ItemID{slot}", f"ItemCount{slot}"))
RowKey = Scalar | tuple[int, int]
Rows = dict[RowKey, tuple[Scalar | None, ...]]


@dataclass(frozen=True, slots=True)
class Table:
    name: str
    key: str | tuple[str, str]
    columns: tuple[str, ...]

    @property
    def keys(self):
        return self.key if isinstance(self.key, tuple) else (self.key,)

    def snapshot(self, database: Connection) -> Rows:
        keys = self.keys
        columns = ", ".join(map(quote, (*keys, *self.columns)))
        rows = database.execute(f"SELECT {columns} FROM {quote(self.name)}").fetchall()
        indexed = {(row[0] if len(keys) == 1 else row[:len(keys)]): row[len(keys):] for row in rows}
        if len(indexed) != len(rows):
            raise ValueError(f"{self.name} contains duplicate keys.")
        return indexed


ITEMS = Table(TABLE, "ItemID", ("Type", "TypeID", *COLUMNS))
PRODUCTS = Table(PRODUCT_TABLE, "ProductCode", ("ProductName", "BuyCost", *PRODUCT_SLOTS))
SKILLS = Table(SKILL_TABLE, ("SkillID", "SkillLevel"), tuple(field.column for field in SKILL_SQL_FIELDS))
NPC_SKILLS = Table(NPC_SKILL_TABLE, ("SkillID", "Rank"), SKILLS.columns)


def item_rows(catalog: Catalog) -> Rows:
    return {
        item_id: (item.family, item.type_id, *(field.sql_value(catalog.value(item, field)) for field in FIELDS if field.column))
        for item_id, item in catalog.items.items()
    }


def product_rows(products: tuple[Product, ...]) -> Rows:
    return {product.code: (product.name, product.cost, *(value for slot in product.bundle for value in slot))
            for product in products}


def key_values(identifier: RowKey):
    return identifier if isinstance(identifier, tuple) else (identifier,)


def reconcile(database: Connection, table: Table, current: Rows, desired: Rows, baseline: Rows | None) -> Rows:
    name = quote(table.name)
    where = " AND ".join(f"{quote(key)} COLLATE BINARY = ?" for key in table.keys)
    compared = current if baseline is None else baseline
    removed = compared.keys() - desired.keys()
    revisions = {identifier: row for identifier, row in desired.items() if compared.get(identifier) != row}
    added = revisions.keys() - current.keys()
    if baseline is not None:
        missing = ((added | removed) & baseline.keys()) - current.keys()
        conflicts = (revisions.keys() - baseline.keys()) & current.keys()
        if missing or conflicts:
            raise ValueError(f"{table.name} has conflicting rows for {', '.join(map(str, sorted(missing | conflicts)))}.")
    expected = current.copy()
    for identifier in removed:
        del expected[identifier]
    for identifier, values in revisions.items():
        if baseline is None or identifier in added:
            expected[identifier] = values
        else:
            expected[identifier] = tuple(
                value if value != previous else saved
                for value, previous, saved in zip(values, baseline[identifier], current[identifier])
            )

    if table == PRODUCTS:
        validate_product_items(database, expected if baseline is None else {key: expected[key] for key in revisions})
    elif table == ITEMS and removed and database.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (PRODUCT_TABLE,),
    ).fetchone():
        for code, row in PRODUCTS.snapshot(database).items():
            for item_id in row[2::2]:
                if item_id in removed:
                    raise ValueError(f"Item {item_id} is still used by cash product {code}. Update Cash.SData before removing it.")
    database.executemany(f"DELETE FROM {name} WHERE {where}", map(key_values, removed))
    if added:
        columns = (*table.keys, *table.columns)
        bindings = [(*key_values(identifier), *expected[identifier]) for identifier in sorted(added)]
        if table == PRODUCTS:
            last, = database.execute(f'SELECT COALESCE(MAX("RowID"), 0) FROM {name}').fetchone()
            if last + len(added) > (1 << 63) - 1:
                raise ValueError("ProductList RowID range is exhausted.")
            columns = ("RowID", *columns)
            bindings = [(row_id, *values) for row_id, values in enumerate(bindings, last + 1)]
        parameters = ", ".join("?" for _ in columns)
        fields = ", ".join(map(quote, columns))
        database.executemany(f"INSERT INTO {name} ({fields}) VALUES ({parameters})", bindings)
    updates = {}
    for identifier in revisions.keys() & current.keys():
        changes = tuple(index for index, (before, after) in enumerate(zip(current[identifier], expected[identifier]))
                        if before != after)
        if changes:
            updates.setdefault(changes, []).append((*[expected[identifier][index] for index in changes], *key_values(identifier)))
    for changes, bindings in updates.items():
        fields = ", ".join(f"{quote(table.columns[index])} = ?" for index in changes)
        database.executemany(f"UPDATE {name} SET {fields} WHERE {where}", bindings)
    if table.snapshot(database) != expected:
        raise ValueError(f"{table.name} failed verification. Changes were rolled back.")
    return expected


def validate_product_items(database: Connection, products: Rows):
    ids = {item_id for item_id, in database.execute(f"SELECT ItemID FROM {quote(TABLE)} WHERE ItemID = Type * 1000 + TypeID")}
    for code, row in products.items():
        occupied = False
        for slot, (item_id, count) in enumerate(zip(row[2::2], row[3::2]), 1):
            if not item_id and not count:
                continue
            if not item_id or item_id not in ids or not isinstance(count, int) or not 1 <= count <= 255:
                raise ValueError(f"Product {code}, slot {slot}: item {item_id} or its quantity is invalid in SQLite.")
            occupied = True
        if not occupied:
            raise ValueError(f"Product {code} needs at least one item in SQLite.")


def rows_hash(rows: Rows) -> str:
    return sha256(json.dumps(sorted(rows.items()), ensure_ascii=False).encode()).hexdigest()


def file_hash(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def sync_folder(path: Path):
    if hasattr(os, "O_DIRECTORY"):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno not in (EBADF, EINVAL, ENOTSUP):
                raise
        finally:
            os.close(descriptor)


def backup_sqlite(path: Path, backup: Path, verify):
    with closing(read_database(path)) as original, closing(connect(backup)) as copy:
        original.backup(copy)
        copy.execute("PRAGMA journal_mode=DELETE")
        if not verify(copy) or copy.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError(f"SQLite backup failed verification. Backup folder\n{backup.parent}")


def replace_file(path: Path, raw: bytes, *, allow_create: bool = False):
    with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}-", delete=False) as file:
        staged = Path(file.name)
        try:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            file.close()
            staged.unlink()
            raise
    try:
        try:
            mode = path.stat().st_mode
        except FileNotFoundError:
            if not allow_create:
                raise
        else:
            staged.chmod(S_IMODE(mode))
        staged.replace(path)
        sync_folder(path.parent)
    finally:
        staged.unlink(missing_ok=True)


def save_model_table(path, catalog, raw, backups, *, format_name, read):
    if read(path) != catalog.raw:
        raise ValueError(f"The {format_name} file changed on disk. Reload before saving.")
    backups.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = Path(mkdtemp(prefix=datetime.now().strftime(f"%Y%m%d-%H%M%S-{format_name}-"), dir=backups))
    backup = folder / path.name
    replace_file(backup, catalog.raw, allow_create=True)
    sync_folder(backups)
    if read(backup) != catalog.raw or read(path) != catalog.raw:
        raise ValueError(f"Could not verify the source and backup. Backup folder\n{folder}")
    replace_file(path, raw)
    saved = type(catalog).open(path)
    if saved.raw != raw:
        raise ValueError(f"Could not verify the saved {format_name}. Backup folder\n{folder}")
    return saved, backup


def resolve_pending(pending: Path, database: Connection, table: Table, source: Path, metadata: dict):
    if metadata["source"] != str(source) or metadata["table"] != table.name:
        raise ValueError(f"Finish recovery for the previous installation first. Backup:\n{pending.parent}")
    raw_hash = file_hash(read_file(source))
    sql_hash = rows_hash(table.snapshot(database))
    if raw_hash == metadata["file_after"] and sql_hash == metadata["sql_after"]:
        pending.replace(pending.with_name("install.json"))
    elif sql_hash == metadata["sql_before"] and raw_hash in (metadata["file_before"], metadata["file_after"]):
        if raw_hash != metadata["file_before"]:
            original = read_file(pending.parent / source.name)
            if file_hash(original) != metadata["file_before"]:
                raise ValueError(f"The recovery copy failed verification. Backup:\n{pending.parent}")
            replace_file(source, original)
        pending.replace(pending.with_name("rolled-back.json"))
    else:
        raise ValueError(f"The targets changed after an interrupted installation. Backup:\n{pending.parent}")


def install_catalog(source: Path, before: bytes, after: bytes, database_path: Path, backups: Path,
                    table: Table, desired: Rows, baseline: Rows | None = None) -> Path:
    source = source.resolve(strict=True)
    database_path = database_path.resolve(strict=True)
    backups.mkdir(mode=0o700, parents=True, exist_ok=True)
    with closing(connect(backups / ".install-lock.sqlite", timeout=5)) as coordinator, closing(
        connect(database_path.as_uri() + "?mode=rw", uri=True, timeout=5)
    ) as database:
        coordinator.execute("BEGIN IMMEDIATE")
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("PRAGMA trusted_schema=OFF")
        database.execute("PRAGMA synchronous=FULL")
        database.execute("BEGIN IMMEDIATE")
        assert_safe_table(database, table.name)
        for pending in backups.glob("*/pending.json"):
            metadata = json.loads(pending.read_text(encoding="utf-8"))
            if metadata["database"] == str(database_path):
                resolve_pending(pending, database, table, source, metadata)
            elif metadata["source"] == str(source):
                raise ValueError(f"This file has a pending installation to another SQLite database. Backup:\n{pending.parent}")
        if read_file(source) != before:
            raise ValueError(f"{source.name} changed after opening. Reopen the catalog.")
        current = table.snapshot(database)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = Path(mkdtemp(prefix=f"{stamp}-{source.stem}-", dir=backups))
        with (backup / source.name).open("wb") as original_file:
            original_file.write(before)
            original_file.flush()
            os.fsync(original_file.fileno())
        backup_sqlite(database_path, backup / "rapture.sqlite", lambda copy: table.snapshot(copy) == current)
        final = reconcile(database, table, current, desired, baseline)
        if read_file(source) != before:
            raise ValueError(f"{source.name} changed during preparation. Backups:\n{backup}")
        metadata = {
            "source": str(source), "database": str(database_path), "table": table.name,
            "operation": "install" if baseline is None else "save",
            "file_before": file_hash(before), "file_after": file_hash(after),
            "sql_before": rows_hash(current), "sql_after": rows_hash(final),
        }
        pending = backup / "pending.json"
        with pending.open("w", encoding="utf-8") as file:
            json.dump(metadata, file, indent=2, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        sync_folder(backup)
        sync_folder(backups)
        try:
            if before != after:
                replace_file(source, after)
            if read_file(source) != after:
                raise ValueError(f"Could not verify {source.name} after writing.")
            database.commit()
        except BaseException:
            database.rollback()
            database.execute("BEGIN IMMEDIATE")
            resolve_pending(pending, database, table, source, metadata)
            raise
        pending.replace(backup / "install.json")
        sync_folder(backup)
        return backup
