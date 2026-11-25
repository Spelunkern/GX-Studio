from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from os import W_OK, access, environ
from pathlib import Path
import sqlite3
from tempfile import mkdtemp

import psutil

from .catalog import Scalar
from .fields import FIELDS, Field
from .install import backup_sqlite, sync_folder
from .workspace import COLUMNS, KEY, TABLE, assert_safe_table, quote, read_database, read_rows

OVERRIDE = "RAPTURE_DB_PATH"
DATA_OVERRIDE = "RAPTURE_DATA_DIR"
SERVICES = {"gx_world", "gx_login"}
DEFAULT_DATABASE = Path("runtime", "rapture.sqlite")
COLUMN_SQL = ", ".join(f"d.{quote(name)}" for name in COLUMNS)
SQL_FIELDS = {field.column: field for field in FIELDS if field.column}


@dataclass(frozen=True, slots=True)
class DatabaseStatus:
    path: Path | None
    status: str
    detail: str
    editable: bool = False

    @property
    def connected(self) -> bool:
        return self.path is not None and self.status in ("Connected", "Read-only")


def probe(path: Path, detail: str) -> DatabaseStatus:
    try:
        path = path.resolve(strict=True)
        with closing(read_database(path)) as database:
            database.execute(f'SELECT d."ItemID", d."Type", d."TypeID", {COLUMN_SQL} FROM "{TABLE}" AS d LIMIT 0')
            try:
                assert_safe_table(database)
            except ValueError as error:
                return DatabaseStatus(path, "Read-only", str(error))
        editable = access(path, W_OK) and access(path.parent, W_OK)
        return DatabaseStatus(path, "Connected" if editable else "Read-only", detail, editable)
    except (OSError, sqlite3.Error, ValueError) as error:
        return DatabaseStatus(path, "Unavailable", str(error))


def runtime_override(root: Path, variable: str = OVERRIDE) -> str:
    try:
        with (root / ".local" / "runtime.env").open(encoding="utf-8-sig") as config:
            override = ""
            for line in config:
                key, equal, value = line.partition("=")
                if equal and key.strip() == variable:
                    override = value.strip()
            return override
    except (OSError, UnicodeError):
        return ""


def process_root(process: psutil.Process, executable: Path) -> Path:
    try:
        return Path(process.cwd())
    except (psutil.AccessDenied, OSError, NotImplementedError):
        return executable.parent.parent if executable.parent.name.lower() == "build" else executable.parent


def process_override(process: psutil.Process, variable: str) -> str:
    try:
        return process.environ().get(variable, "")
    except (psutil.AccessDenied, OSError, NotImplementedError):
        return ""


def process_path(process: psutil.Process, cwd: Path) -> Path | None:
    override = process_override(process, OVERRIDE)
    try:
        opened = {
            path for file in process.open_files()
            if (path := Path(file.path)).suffix.lower() in (".sqlite", ".sqlite3", ".db")
            and not (path.name.lower() == "rapture.sqlite" and path.parent.name.lower() == "data")
        }
    except (psutil.AccessDenied, OSError, NotImplementedError):
        opened = set()
    if override:
        return cwd / override
    configured = (cwd / (runtime_override(cwd) or DEFAULT_DATABASE)).resolve()
    if configured in opened:
        return configured
    candidates = {path.resolve() for path in opened if probe(path, "").connected}
    if len(candidates) == 1:
        return candidates.pop()
    if not candidates and configured.is_file():
        return configured
    return None


def gx_processes():
    for process in psutil.process_iter(("name", "exe"), ad_value=None):
        name = Path(process.info["name"] or "").stem.lower()
        if name not in SERVICES:
            continue
        executable = process.info["exe"]
        if not executable or Path(executable).stem.lower() != name:
            continue
        yield process, name, Path(executable)


def running_databases(server_data: Path | None = None) -> dict[Path, set[str]]:
    paths: dict[Path, set[str]] = {}
    for process, name, executable in gx_processes():
        try:
            cwd = process_root(process, executable)
            if server_data is not None:
                folder = cwd / (process_override(process, DATA_OVERRIDE) or runtime_override(cwd, DATA_OVERRIDE) or "data")
                if folder.resolve() != server_data:
                    continue
            if (path := process_path(process, cwd)) is not None:
                paths.setdefault(path.resolve(), set()).add(name)
        except (psutil.Error, OSError, RuntimeError, ValueError):
            continue
    return paths


def server_data_folder(folder: Path) -> Path | None:
    for candidate in (folder, folder / (runtime_override(folder, DATA_OVERRIDE) or "data")):
        if (candidate / "game").is_dir():
            return candidate.resolve()
    return None


def server_database_path(folder: Path) -> Path:
    root = folder
    if (folder / "game").is_dir() and folder.name.casefold() == "data":
        root = folder.parent
    return (root / (runtime_override(root) or DEFAULT_DATABASE)).resolve()


def connect_gx(manual: Path | None = None, server_folder: Path | None = None) -> DatabaseStatus:
    if manual is not None:
        return probe(manual, "Selected manually")
    if server_folder is not None:
        data = server_data_folder(server_folder)
        paths = running_databases(data) if data is not None else {}
    else:
        paths = running_databases()
    if len(paths) == 1:
        path, services = next(iter(paths.items()))
        service_names = {"gx_world": "GX World", "gx_login": "GX Login"}
        names = " + ".join(service_names[service] for service in sorted(services, reverse=True))
        return probe(path, f"Detected from {names}")
    if paths:
        return DatabaseStatus(None, "Select database", "Multiple GX runtime databases detected.")
    if server_folder is not None:
        candidate = server_database_path(server_folder)
        if candidate.is_file():
            return probe(candidate, "Runtime database in the selected GX Server folder")
        return DatabaseStatus(None, "Disconnected", f"No SQLite runtime database found for the selected Server Data.\n{candidate}")
    if environ.get(OVERRIDE):
        return probe(Path(environ[OVERRIDE]), f"Configured by {OVERRIDE}")
    return DatabaseStatus(None, "Disconnected", "No running GX process with an identifiable SQLite database.")


def detect_server_data(database: Path | None) -> Path | None:
    if database is not None:
        found = set()
        for process, name, executable in gx_processes():
            if name != "gx_world":
                continue
            try:
                root = process_root(process, executable)
                path = process_path(process, root)
                if path is not None and path.resolve() == database:
                    folder = root / (process_override(process, DATA_OVERRIDE) or runtime_override(root, DATA_OVERRIDE) or "data")
                    if (folder / "game").is_dir():
                        found.add(folder.resolve())
            except (psutil.Error, OSError, RuntimeError, ValueError):
                continue
        if found:
            return next(iter(found)) if len(found) == 1 else None
        for root in (database.parent, database.parent.parent):
            if server_database_path(root) == database and (folder := server_data_folder(root)) is not None:
                return folder
        return None
    if (override := environ.get(DATA_OVERRIDE)) and (Path(override) / "game").is_dir():
        return Path(override).resolve()
    return None


def read_catalog(path: Path) -> dict[int, dict[str, Scalar | None]]:
    with closing(read_database(path)) as database:
        return read_rows(database)


def parse_sql_value(field: Field, value: str) -> Scalar:
    if not field.size:
        return field.parse(value)
    limits = {"Range": 32767, "Count": 65535, "Reqluc": 2147483647}
    maximum = limits.get(field.column, field.sql_max)
    if maximum is not None:
        minimum = 0 if field.column in ("Count", "Buy", "Sell") else -maximum - 1
        number = value.strip()
        digits = number.removeprefix("-")
        if not digits.isascii() or not digits.isdecimal() or len(digits) > 10:
            raise ValueError(f"Expected an integer between {minimum} and {maximum}.")
        parsed = int(number)
        if not minimum <= parsed <= maximum:
            raise ValueError(f"Expected an integer between {minimum} and {maximum}.")
        return parsed
    return field.parse(value)


def update_item(
    path: Path, item_id: int, before: dict[str, Scalar | None], changes: dict[str, Scalar],
    *, backups: Path | None = None,
) -> Path | None:
    if not changes:
        return
    # Validate again at the database boundary and only interpolate known identifiers.
    expected = {name: parse_sql_value(SQL_FIELDS[name], str(value)) for name, value in changes.items()}
    identity = (item_id, *divmod(item_id, 1000))
    columns = tuple(expected)
    selected = ", ".join(map(quote, columns))
    assignments = ", ".join(f"{quote(name)} = ?" for name in columns)
    folder = None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True)) as database, database:
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("PRAGMA trusted_schema=OFF")
        database.execute("BEGIN IMMEDIATE")
        assert_safe_table(database)
        # Qualify columns so SQLite cannot treat a missing identifier as a string literal.
        selection = ", ".join(f"d.{quote(name)}" for name in columns)
        query = f'SELECT {selection} FROM "{TABLE}" AS d WHERE {KEY}'
        current = [tuple(before[name] for name in columns)]
        if database.execute(query, identity).fetchall() != current:
            raise ValueError(f"Item {item_id} changed in SQLite. Refresh the table and reopen the item.")
        if backups is not None:
            backups.mkdir(mode=0o700, parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            folder = Path(mkdtemp(prefix=f"{stamp}-SQLite-Item-{item_id}-", dir=backups))
            backup_sqlite(path, folder / "rapture.sqlite", lambda copy: copy.execute(query, identity).fetchall() == current)
            sync_folder(folder)
            sync_folder(backups)
        result = database.execute(
            f'UPDATE "{TABLE}" SET {assignments} WHERE {KEY}', (*expected.values(), *identity)
        )
        if result.rowcount != 1 or database.execute(query, identity).fetchall() != [tuple(expected.values())]:
            raise ValueError(f"Could not verify the update for item {item_id} ({selected}).")
    return folder
