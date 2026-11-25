from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection, connect
from tempfile import TemporaryDirectory
from uuid import uuid4

from .catalog import Catalog, Edits, Item, Scalar, read_file
from .fields import FIELDS, FIELDS_BY_NAME

TABLE = "PS_GameDefs_Items"
SKILL_TABLE = "PS_GameDefs_Skills"
NPC_SKILL_TABLE = "PS_GameDefs_NpcSkills"
KEY = '"ItemID" = ? AND "Type" = ? AND "TypeID" = ?'
COLUMNS = tuple(field.column for field in FIELDS if field.column)
NPC_IDENTITY_TRIGGER = """
CREATE TRIGGER PS_GameDefs_NpcSkills_identity
AFTER INSERT ON PS_GameDefs_NpcSkills WHEN NEW.RowID = 0
BEGIN
    UPDATE PS_GameDefs_NpcSkills
    SET RowID = (SELECT COALESCE(MAX(RowID), 0) + 1
                 FROM PS_GameDefs_NpcSkills WHERE _rowid_ <> NEW._rowid_)
    WHERE _rowid_ = NEW._rowid_;
END"""


def identity_trigger(table: str) -> str:
    return (
        f'CREATE TRIGGER "{table}_identity" AFTER INSERT ON "{table}" '
        f'WHEN NEW."RowID"=0 BEGIN UPDATE "{table}" SET "RowID"='
        f'(SELECT COALESCE(MAX("RowID"),0)+1 FROM "{table}" WHERE _rowid_<>NEW._rowid_) '
        'WHERE _rowid_=NEW._rowid_; END'
    )


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def read_database(path: Path) -> Connection:
    return connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def read_rows(database: Connection) -> dict[int, dict[str, Scalar | None]]:
    columns = ", ".join(f"d.{quote(column)}" for column in COLUMNS)
    records = database.execute(
        f'SELECT d."ItemID", {columns} FROM "{TABLE}" AS d '
        'WHERE d."Type" BETWEEN 1 AND 255 AND d."TypeID" BETWEEN 1 AND 255 '
        'AND d."ItemID" = d."Type" * 1000 + d."TypeID"'
    ).fetchall()
    counts = Counter(record[0] for record in records)
    return {record[0]: dict(zip(COLUMNS, record[1:])) for record in records if counts[record[0]] == 1}


def assert_safe_table(database: Connection, table: str = TABLE) -> None:
    schema = database.execute(
        "SELECT type, sql FROM sqlite_schema WHERE tbl_name = ? COLLATE NOCASE", (table,)
    ).fetchall()
    if not any(kind == "table" and ddl and ddl.upper().startswith("CREATE TABLE") for kind, ddl in schema):
        raise ValueError(f"{table} is not a GX SQLite table.")
    allowed = {identity_trigger(table)} if table in (
        TABLE, SKILL_TABLE, NPC_SKILL_TABLE, "PS_GameDefs_Mobs", "PS_GameDefs_MobItems",
    ) else set()
    if table == NPC_SKILL_TABLE:
        allowed.add(NPC_IDENTITY_TRIGGER.strip())
    if any(kind == "trigger" and ddl not in allowed for kind, ddl in schema):
        raise ValueError(f"{table} has custom triggers that need review.")
    if database.execute(
        "SELECT 1 FROM sqlite_schema AS m, pragma_foreign_key_list(m.name) AS f "
        "WHERE m.type = 'table' AND f.[table] = ? COLLATE NOCASE LIMIT 1", (table,)
    ).fetchone():
        raise ValueError(f"{table} has external references that need review.")


@dataclass(frozen=True, slots=True)
class Change:
    item: Item
    field: str
    before: Scalar
    after: Scalar
    column: str
    sql_before: Scalar | None
    sql_after: Scalar | None


class Workspace:
    def __init__(self, source: Path, database_path: Path | None = None):
        self.source = source.resolve(strict=True)
        self.catalog = Catalog.open(self.source)
        self.database_path = database_path.resolve(strict=True) if database_path else None
        self.sql_rows: dict[int, dict[str, Scalar | None]] = {}
        if self.database_path:
            with closing(read_database(self.database_path)) as database:
                self.sql_rows = read_rows(database)

    def changes(self, edits: Edits, *, include_sql: bool = True) -> list[Change]:
        changes = []
        for item_id, values in sorted(edits.items()):
            item = self.catalog.items[item_id]
            for name, value in values.items():
                field = FIELDS_BY_NAME[name]
                before = self.catalog.value(item, field)
                after = field.parse(str(value))
                if before == after:
                    continue
                column = field.column if self.database_path and include_sql else ""
                row = self.sql_rows.get(item_id)
                if column and row is None:
                    raise ValueError(f"Item {item_id} has no unique SQLite row for {column}.")
                changes.append(Change(
                    item, name, before, after, column,
                    row[column] if column and row is not None else None,
                    field.sql_value(after) if column else None,
                ))
        return changes

    def _copy_database(self, target: Path, changes: list[Change]) -> None:
        assert self.database_path is not None
        with closing(read_database(self.database_path)) as source, closing(connect(target)) as copy:
            source.backup(copy, pages=256, sleep=0.05)
            copy.execute("PRAGMA journal_mode=DELETE")
            copy.execute("PRAGMA foreign_keys=ON")
            copy.execute("PRAGMA trusted_schema=OFF")
            with copy:
                self._apply_database(copy, [change for change in changes if change.column])
            if copy.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("The SQLite copy failed its integrity check.")

    def _apply_database(self, database: Connection, changes: list[Change]) -> None:
        if not changes:
            return
        assert_safe_table(database)
        current = read_rows(database)
        for change in changes:
            item = change.item
            row = current.get(item.item_id)
            if row is None or row[change.column] != change.sql_before:
                raise ValueError(f"SQLite changed for {item.item_id}/{change.column}. Reopen the catalog.")
        for change in changes:
            item = change.item
            identity = (item.item_id, item.family, item.type_id)
            column = quote(change.column)
            result = database.execute(
                f'UPDATE "{TABLE}" SET {column} = ? WHERE {KEY}', (change.sql_after, *identity)
            )
            stored = database.execute(f'SELECT {column} FROM "{TABLE}" WHERE {KEY}', identity).fetchall()
            if result.rowcount != 1 or stored != [(change.sql_after,)]:
                raise ValueError(f"Could not verify item {item.item_id} in SQLite.")

    def export(self, parent: Path, edits: Edits) -> Path:
        changes = self.changes(edits)
        if not changes:
            raise ValueError("Make at least one change before exporting.")
        if read_file(self.source) != self.catalog.raw:
            raise ValueError("Item.SData changed after opening. Reopen the catalog.")
        raw = self.catalog.encode(edits)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = parent / f"GX Items {stamp}-{uuid4().hex[:8]}"
        with TemporaryDirectory(prefix=".gx-items-", dir=parent) as temporary:
            stage = Path(temporary) / "export"
            item_path = stage / "data" / "Item" / "Item.SData"
            item_path.parent.mkdir(parents=True)
            item_path.write_bytes(raw)
            if self.database_path:
                self._copy_database(stage / "rapture.sqlite", changes)
            if read_file(self.source) != self.catalog.raw:
                raise ValueError("Item.SData changed during export. Reopen the catalog.")
            if destination.exists():
                raise FileExistsError(destination)
            stage.rename(destination)
        return destination
