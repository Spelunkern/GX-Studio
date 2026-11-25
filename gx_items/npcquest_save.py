import json
import os
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from sqlite3 import connect
from tempfile import mkdtemp

from .catalog import read_file
from .install import file_hash, replace_file, sync_folder
from .npcquest_data import NPCQuestCatalog, QuestData, server_catalog

FILENAME = NPCQuestCatalog.filename
PENDING = "quest-pending.json"


@dataclass(frozen=True, slots=True)
class FileChange:
    path: Path
    before: bytes
    after: bytes


def write_synced(path: Path, raw: bytes):
    with path.open("xb") as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())


def validate_targets(paths):
    for path in paths:
        if not path.is_file():
            raise ValueError(f"NpcQuest requires an existing file at {path}.")
    if len(paths) == 2 and paths[0].samefile(paths[1]):
        raise ValueError("Select two separate files for client and server NpcQuest.")


def finish_quest_write(folder: Path, targets: dict, *, rollback=False):
    paths = [Path(entry["path"]) for entry in targets.values()]
    validate_targets(paths)
    writes = []
    for label, entry in targets.items():
        path = Path(entry["path"])
        observed = file_hash(read_file(path))
        if observed not in (entry["before"], entry["after"]):
            raise ValueError(f"{path} changed during synchronization. Verified backups are in {folder}.")
        version = "before" if rollback else "after"
        if observed != entry[version]:
            raw = read_file(folder / f"{label}-{version}.SData")
            if file_hash(raw) != entry[version]:
                raise ValueError(f"NpcQuest recovery data failed verification in {folder}.")
            writes.append((path, observed, raw))
    for path, observed, raw in writes:
        if file_hash(read_file(path)) != observed:
            raise ValueError(f"{path} changed during synchronization. Verified backups are in {folder}.")
        replace_file(path, raw)
        if read_file(path) != raw:
            raise ValueError(f"Could not verify the saved NpcQuest file {path}.")
    manifest = "quest-rolled-back.json" if rollback else "quest-save.json"
    (folder / PENDING).replace(folder / manifest)
    sync_folder(folder)


def publish_quest_files(changes: dict[str, FileChange], backups: Path) -> Path:
    paths = [change.path for change in changes.values()]
    validate_targets(paths)
    backups.mkdir(mode=0o700, parents=True, exist_ok=True)
    with closing(connect(backups / ".install-lock.sqlite", timeout=5)) as coordinator:
        coordinator.execute("BEGIN IMMEDIATE")
        for pending in backups.glob(f"*/{PENDING}"):
            targets = json.loads(pending.read_text(encoding="utf-8"))
            if {str(path) for path in paths} & {entry["path"] for entry in targets.values()}:
                finish_quest_write(pending.parent, targets)
        for change in changes.values():
            if read_file(change.path) != change.before:
                raise ValueError(f"{change.path} changed after opening. Reload NpcQuest before continuing.")
        if all(change.before == change.after for change in changes.values()):
            raise ValueError("The selected NpcQuest files already contain these definitions.")

        folder = Path(mkdtemp(prefix=datetime.now().strftime("%Y%m%d-%H%M%S-NpcQuest-"), dir=backups))
        targets = {}
        for label, change in changes.items():
            for version, raw in (("before", change.before), ("after", change.after)):
                copy = folder / f"{label}-{version}.SData"
                write_synced(copy, raw)
                if read_file(copy) != raw:
                    raise ValueError(f"NpcQuest backup failed verification in {folder}.")
            targets[label] = {"path": str(change.path), "before": file_hash(change.before), "after": file_hash(change.after)}
        prepared = folder / "quest-prepared.json"
        write_synced(prepared, json.dumps(targets, indent=2, ensure_ascii=False).encode("utf-8"))
        prepared.replace(folder / PENDING)
        sync_folder(folder)
        sync_folder(backups)
        try:
            finish_quest_write(folder, targets)
        except BaseException as error:
            if (folder / PENDING).is_file():
                try:
                    finish_quest_write(folder, targets, rollback=True)
                except (OSError, ValueError) as rollback_error:
                    raise ValueError(f"NpcQuest write interrupted. Recovery files are in {folder}. {rollback_error}") from error
            raise
        return folder


def save_quest_catalog(client_path: Path, client: NPCQuestCatalog, draft: QuestData, backups: Path,
                       server_path: Path | None = None, server: NPCQuestCatalog | None = None, *, full: bool = False):
    client_path = client_path.resolve(strict=True)
    client_next = client.encode(draft)
    changes = {"client": FileChange(client_path, client.raw, client_next)}
    server_next = None
    if server_path is not None and server is not None:
        server_path = server_path.resolve(strict=True)
        validate_targets([client_path, server_path])
        desired = server_catalog(client.data, draft, server.data, full=full)
        server_next = server.encode(desired)
        changes["server"] = FileChange(server_path, server.raw, server_next)
    elif full:
        raise ValueError("Select the server NpcQuest file before installing.")
    return publish_quest_files(changes, backups), NPCQuestCatalog(client_next), NPCQuestCatalog(server_next) if server_next else None
