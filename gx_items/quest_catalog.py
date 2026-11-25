from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from sqlite3 import Error as SQLiteError
from struct import Struct, iter_unpack

from . import seed
from .catalog import Cursor, decode_text, read_file
from .equipment_models import ModelAssets
from .workspace import read_database

QUEST_ITEM_FAMILIES = frozenset((27, 28, 29, 99))
QUEST_ITEMS = Struct("<BBB")
MOB_TARGETS = Struct("<HBHB")
MONSTER_RECORD = Struct("<29xH")
EMPTY_ITEM_FAMILY = bytes(256 * 2 * 4)


@dataclass(frozen=True, slots=True)
class Quest:
    name: str
    objectives: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Mob:
    name: str
    quest_item: int


def quest_links(cursor: Cursor) -> bytes:
    return cursor.take(2 * cursor.word())


def skip_npcs(cursor: Cursor):
    for group in range(1, 14):
        count = cursor.word()
        if count > 65535:
            raise ValueError("Invalid NPC count in NpcQuest.SData.")
        for _ in range(count):
            cursor.take(20 if group == 1 else 19)
            cursor.string()  # Name
            cursor.string()  # Welcome text
            if group == 1:
                cursor.take(2 * cursor.word())  # Merchant stock
            elif group == 2:
                for _ in range(3):
                    cursor.take(14)  # Destination map and position
                    cursor.string()
                    cursor.take(4)  # Cost
            quest_links(cursor)
            quest_links(cursor)


def read_quests(raw: bytes) -> tuple[dict[int, Quest], dict[int, set[int]]]:
    plain, _ = seed.decode(raw)
    cursor = Cursor(plain)
    skip_npcs(cursor)
    by_item: dict[int, set[int]] = defaultdict(set)
    for family in range(256):
        if plain.startswith(EMPTY_ITEM_FAMILY, cursor.position):
            cursor.position += len(EMPTY_ITEM_FAMILY)
            continue
        for type_id in range(256):
            start, end = quest_links(cursor), quest_links(cursor)
            if family and type_id and (start or end):
                by_item[family * 1000 + type_id].update(row for row, in iter_unpack("<H", start + end))

    count = cursor.word()
    if count > 65535:
        raise ValueError("Invalid quest count in NpcQuest.SData.")
    quests = {}
    for quest_id in range(1, count + 1):
        cursor.take(2)  # GX uses the one-based row ID for quest links.
        name = decode_text(cursor.string()).strip()
        cursor.string()  # Summary
        cursor.take(50)  # Prerequisites and timers
        start_type = cursor.take(4)[0]
        family, type_id = cursor.take(2)
        if start_type in (2, 3, 4) and family and type_id:
            by_item[family * 1000 + type_id].add(quest_id)
        starting_items = cursor.take(3 * QUEST_ITEMS.size)
        end_type = cursor.take(4)[0]
        farm_items = cursor.take(3 * QUEST_ITEMS.size)
        for family, type_id, amount in QUEST_ITEMS.iter_unpack(starting_items + (farm_items if end_type == 1 else b"")):
            if family and type_id and amount:
                by_item[family * 1000 + type_id].add(quest_id)
        cursor.take(1)  # PvP kills
        mob_a, count_a, mob_b, count_b = MOB_TARGETS.unpack(cursor.take(MOB_TARGETS.size))
        objectives = tuple(mob for mob, amount in ((mob_a, count_a), (mob_b, count_b)) if mob and amount and end_type == 2)
        result_type, user_select = cursor.take(2)
        for index in range(3):
            result = cursor.take(26)
            # Extra outcomes must be selectable or conditional in GX.
            conditional = any(result[2:3] + result[4:14])
            usable = index == 0 or (result_type != 1 and (index < user_select if user_select else conditional))
            family, type_id = result[22:24]
            if usable and family in QUEST_ITEM_FAMILIES and type_id:
                by_item[family * 1000 + type_id].add(quest_id)
        for _ in range(7):
            cursor.string()  # Result and journal text
        quests[quest_id] = Quest(name, objectives)
    if cursor.position != len(plain):
        raise ValueError("Unexpected bytes after the quest catalog.")
    return quests, dict(by_item)


def read_monsters(raw: bytes) -> dict[int, Mob]:
    plain, _ = seed.decode(raw)
    cursor = Cursor(plain)
    count = cursor.word()
    if count > 65536:
        raise ValueError("Invalid monster count in Monster.SData.")
    mobs = {}
    for mob_id in range(count):
        name = decode_text(cursor.string()).strip()
        quest_item, = MONSTER_RECORD.unpack(cursor.take(MONSTER_RECORD.size))
        mobs[mob_id] = Mob(name, quest_item)
    if cursor.position != len(plain):
        raise ValueError("Unexpected bytes after the monster catalog.")
    return mobs


def mob_quest_item(index: int) -> int | None:
    # GX's Mobs.QuestItemID is an index with a byte-sized TypeID.
    family = 27 if index <= 1000 else 28 if index <= 2000 else 29
    type_id = (index - 1000 * (family - 27)) & 255
    return family * 1000 + type_id if index > 0 and type_id else None


@lru_cache(maxsize=8)
def load_quests(path: Path, revision: tuple[int, int]):
    return read_quests(read_file(path))


@lru_cache(maxsize=8)
def load_monsters(path: Path, revision: tuple[int, int]):
    return read_monsters(read_file(path))


@lru_cache(maxsize=8)
def load_database_mobs(path: Path, revision: tuple[int, int], wal_revision: tuple[int, int] | None):
    with closing(read_database(path)) as database:
        rows = database.execute('SELECT "MobID", "MobName", "QuestItemID" FROM "PS_GameDefs_Mobs"')
        return {mob_id: Mob(str(name or "").strip(), int(index or 0)) for mob_id, name, index in rows}


def file_revision(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size


@dataclass(frozen=True, slots=True)
class QuestReferences:
    quests: tuple[tuple[int, str], ...]
    mobs: tuple[tuple[int, str, str], ...]


def quest_references(item_id: int, assets: ModelAssets | None, database: Path | None = None, *,
                     server: bool = False, server_quest: Path | None = None):
    client_quests = assets.resolve("Npc/NpcQuest.SData") if assets else None
    quest_paths = []
    if server_quest is not None:
        quest_paths.append(server_quest)
    elif database is not None:
        quest_paths.extend((database.parent / "game/NpcQuest.SData", database.parent.parent / "data/game/NpcQuest.SData"))
    if client_quests:
        quest_paths.insert(len(quest_paths) if server else 0, client_quests)
    quests, by_item = {}, {}
    for path in quest_paths:
        if (revision := file_revision(path)) is not None:
            try:
                quests, by_item = load_quests(path, revision)
                break
            except (OSError, ValueError):
                continue

    client_mobs = {}
    client_monsters = assets.resolve("Monster/Monster.SData") if assets else None
    if client_monsters and (revision := file_revision(client_monsters)) is not None:
        try:
            client_mobs = load_monsters(client_monsters, revision)
        except (OSError, ValueError):
            pass
    mobs = client_mobs
    if database is not None and (revision := file_revision(database)) is not None:
        try:
            wal = database.with_name(f"{database.name}-wal")
            mobs = load_database_mobs(database, revision, file_revision(wal))
        except (OSError, SQLiteError, TypeError, ValueError):
            pass

    related = [(qid, quests[qid]) for qid in sorted(by_item.get(item_id, ())) if qid in quests]
    objectives = {mid for _, quest in related for mid in quest.objectives}
    drops = {mid for mid, mob in mobs.items() if mob_quest_item(mob.quest_item) == item_id}
    names = {mid: mob.name for mid in drops | objectives if (mob := mobs.get(mid) or client_mobs.get(mid))}
    return QuestReferences(
        tuple((qid, quest.name) for qid, quest in related),
        tuple(
            (mid, names.get(mid, ""),
             "Drop + objective" if mid in drops and mid in objectives else
             "Item drop" if mid in drops else "Quest objective")
            for mid in sorted(drops | objectives)
        ),
    )
