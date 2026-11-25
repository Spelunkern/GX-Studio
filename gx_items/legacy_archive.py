"""Read client-only legacy SAH/SAF archives and loose data directories."""

from contextlib import contextmanager
from dataclasses import dataclass, replace
import ntpath
from pathlib import Path, PureWindowsPath
from shutil import rmtree
from struct import Struct
from tempfile import mkdtemp

from .catalog import Cursor

SAF_ENTRY = Struct("<qii")
MAX_ENTRIES = 1 << 20
BLOCK_SIZE = 1 << 20
WINDOWS_RESERVED = getattr(ntpath, "isreserved", lambda name: PureWindowsPath(name).is_reserved())


def safe_component(name: str):
    if (not name or name in (".", "..") or name.endswith((" ", ".")) or WINDOWS_RESERVED(name)
            or any(char in '<>:"/\\|?*' or ord(char) < 32 for char in name)):
        raise ValueError(f"Invalid client path component {name!r}.")
    return name


@contextmanager
def new_output_folder(destination):
    if destination.exists() or destination.is_symlink():
        raise ValueError("Choose a new output folder.")
    staging = Path(mkdtemp(prefix=".gx-data-", dir=destination.parent))
    try:
        yield staging
        if destination.exists() or destination.is_symlink():
            raise ValueError("The output folder was created during the operation.")
        staging.rename(destination)
    finally:
        if staging.exists():
            rmtree(staging)


@dataclass(frozen=True, slots=True)
class LegacyEntry:
    path: Path
    size: int
    offset: int = 0
    renamed: bool = False


@dataclass(frozen=True, slots=True)
class LegacySource:
    path: Path
    entries: tuple[LegacyEntry, ...]
    archive: Path | None = None
    single_file: bool = False

    @classmethod
    def open(cls, path: Path):
        path = path.resolve()
        if path.is_dir():
            indexes = [item for item in path.iterdir() if item.name.casefold() == "data.sah" and item.is_file()]
            if len(indexes) > 1:
                raise ValueError("Select the SAH file to use from this folder.")
            if indexes:
                return cls.open(indexes[0])
            entries = []
            for item in path.rglob("*"):
                if item.is_symlink() or not item.is_file():
                    continue
                relative = item.relative_to(path)
                for name in relative.parts:
                    safe_component(name)
                entries.append(LegacyEntry(relative, item.stat().st_size))
            return cls(path, tuple(sorted(entries, key=lambda entry: entry.path.as_posix().casefold())))

        if path.suffix.casefold() == ".sdata":
            return cls(path, (LegacyEntry(Path(path.name), path.stat().st_size),), single_file=True)
        if path.suffix.casefold() != ".sah":
            raise ValueError("Select a client folder, SAH index or SData file.")
        matches = [item for item in path.parent.iterdir()
                   if item.name.casefold() == path.with_suffix(".saf").name.casefold() and item.is_file()]
        if len(matches) != 1:
            raise ValueError("Select a SAH index with its matching SAF file in the same folder.")
        archive = matches[0]
        archive_size = archive.stat().st_size
        with path.open("rb") as stream:
            raw = stream.read(64 * BLOCK_SIZE + 1)
        if len(raw) > 64 * BLOCK_SIZE:
            raise ValueError("The SAH index exceeds 64 MiB.")
        cursor = Cursor(raw, path.name)
        if cursor.take(3) != b"SAH":
            raise ValueError("Invalid SAH signature.")
        cursor.word()  # Archive revision, independent of the client episode.
        cursor.word()  # File count in the header may include older entries.
        cursor.take(40)

        def read_name():
            size = cursor.word()
            if size > 1024:
                raise ValueError("Invalid SAH name length.")
            name = cursor.take(size).partition(b"\0")[0]
            try:
                return name.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    return name.decode("cp1252")
                except UnicodeDecodeError:
                    return name.decode("latin-1")

        remaining = MAX_ENTRIES
        tree_start = cursor.position

        def count(mask=0):
            nonlocal remaining
            size = cursor.word() ^ mask
            remaining -= size
            if remaining < 0:
                raise ValueError("Too many SAH entries.")
            return size

        def directory(parent, depth, file_count_mask):
            if depth > 64:
                raise ValueError("The SAH directory tree is too deep.")
            folder_name = read_name()
            prefix = parent / safe_component(folder_name) if depth else parent
            files = {}
            for _ in range(count(file_count_mask)):
                name = safe_component(read_name())
                offset, length, _ = SAF_ENTRY.unpack(cursor.take(SAF_ENTRY.size))
                if not 0 <= offset <= archive_size or not 0 <= length <= archive_size - offset:
                    raise ValueError(f"Invalid SAF range for {prefix / name}.")
                files[name.casefold()] = LegacyEntry(prefix / name, length, offset)
            children = {}
            for _ in range(count()):
                child_name, child_files = directory(prefix, depth + 1, file_count_mask)
                children[child_name.casefold()] = child_files
            # Archive file and directory indexes are separate. Preserve both when they share a name.
            occupied = files.keys() | children.keys()
            for key in files.keys() & children.keys():
                entry = files[key]
                output_name = entry.path.name + ".saf-file"
                while output_name.casefold() in occupied:
                    output_name += ".saf-file"
                occupied.add(output_name.casefold())
                files[key] = replace(entry, path=prefix / output_name, renamed=True)
            return folder_name, [*files.values(), *(entry for child in children.values() for entry in child)]

        def read_tree(file_count_mask):
            nonlocal remaining
            remaining = MAX_ENTRIES
            cursor.position = tree_start
            _, entries = directory(Path(), 0, file_count_mask)
            if any(raw[cursor.position:]):
                raise ValueError("Unexpected data after the SAH directory tree.")
            return entries

        try:
            entries = read_tree(0)
        except ValueError as error:
            try:
                entries = read_tree(0x55)
            except ValueError:
                raise error from None
        return cls(path, tuple(sorted(entries, key=lambda entry: entry.path.as_posix().casefold())), archive)

    def entry_source(self, entry: LegacyEntry):
        return self.path if self.single_file else self.path / entry.path

    def validate_destination(self, destination: Path):
        if self.archive is None and not self.single_file and destination.resolve().is_relative_to(self.path):
            raise ValueError("Choose an output folder outside the source data.")

    def read_catalog(self, entry: LegacyEntry, limit: int):
        if entry.size > limit:
            raise ValueError(f"{entry.path.name} exceeds the catalog size limit.")
        with (self.archive or self.entry_source(entry)).open("rb") as stream:
            stream.seek(entry.offset)
            data = stream.read(entry.size)
        if len(data) != entry.size:
            raise ValueError(f"{entry.path} was truncated after loading the client data.")
        return data

    def extract(self, destination: Path):
        destination = destination.absolute()
        self.validate_destination(destination)
        with new_output_folder(destination) as staging:
            if self.archive:
                with self.archive.open("rb") as stream:
                    for entry in self.entries:
                        self.copy_entry(stream, entry, staging)
            else:
                for entry in self.entries:
                    with self.entry_source(entry).open("rb") as stream:
                        self.copy_entry(stream, entry, staging)
        return len(self.entries)

    @staticmethod
    def copy_entry(stream, entry, staging):
        target = staging / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        stream.seek(entry.offset)
        remaining = entry.size
        with target.open("xb") as output:
            while remaining:
                block = stream.read(min(BLOCK_SIZE, remaining))
                if not block:
                    raise ValueError(f"{entry.path} was truncated during extraction.")
                output.write(block)
                remaining -= len(block)
