"""Independent client and server monster definitions."""

from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from sqlite3 import connect
from tempfile import mkdtemp

from . import seed
from .catalog import Cursor, WORD, decode_text, read_file
from .install import backup_sqlite, sync_folder
from .workspace import assert_safe_table, quote, read_database

TABLE = "PS_GameDefs_Mobs"
DROPS = "PS_GameDefs_MobItems"
U8, U16, U32 = 255, 65535, 4294967295
BODY = 31
NAME_BYTES = 255
ELEMENTS = ("Neutral", "Fire", "Water", "Earth", "Wind")
SCALES = ("0.6×", "1×", "1.3×", "1.6×", "2×", "5×", "0.4×")
RESPAWNS = ("5 seconds", "30 seconds", "1 minute", "3 minutes", "5 minutes", "10 minutes",
            "15 minutes", "30 minutes", "1 hour", "4 hours", "12 hours", "24 hours",
            "3 days", "2 hours", "Immediate", "7 days")
CONDITIONS = ("Disabled", "Always", "Always (2)", "20–40% HP", "At most 5% HP", "Below 20% HP", "Weakest target")


@dataclass(frozen=True, slots=True)
class MobField:
    key: str
    label: str
    group: str
    maximum: int = U32
    minimum: int = 0
    note: str = ""
    offset: int = 0
    width: int = 0
    choices: tuple[str, ...] = ()
    read_only: bool = False

    @property
    def is_text(self):
        return self.key in ("Name", "MobName")

    @property
    def range_text(self):
        return f"Max {NAME_BYTES} UTF-8 bytes" if self.is_text else f"{self.minimum:,}–{self.maximum:,}"

    def parse(self, value):
        if self.is_text:
            if not isinstance(value, str) or "\0" in value or len(value.encode("utf-8")) > NAME_BYTES:
                raise ValueError(f"{self.label} accepts up to {NAME_BYTES} UTF-8 bytes and no NUL characters.")
            return value
        digits = str(value).strip()
        if digits.isascii() and digits.isdecimal() and len(digits) <= 10:
            number = int(digits)
            if self.minimum <= number <= self.maximum:
                return number
        raise ValueError(f"{self.label} must be an integer in {self.range_text}.")


def client(key, label, offset, width, note="", *, choices=()):
    maximum = len(choices) - 1 if choices else (1 << (width * 8)) - 1
    return MobField(key, label, "", maximum, offset=offset, width=width, note=note, choices=choices)


CLIENT_FIELDS = (
    MobField("Name", "Name in the client", "", note="Localized name shown in game."),
    client("Model", "MON model index", 0, 2, "Index in monster.MON."),
    client("Level", "Displayed level", 2, 2, "Client presentation. Combat level comes from SQLite."),
    client("AI", "Client AI metadata", 4, 1, "Stored client value. GX World controls AI."),
    client("HP", "Client HP metadata", 5, 4, "Live HP comes from GX World."),
    client("Day", "Visual scale", 9, 1, "Native Day byte controls model scale.", choices=SCALES),
    client("Size", "Client targeting size", 10, 1, "Used for client targeting and approach. SQLite controls server reach."),
    client("Attrib", "Displayed element", 11, 1, "Combat element comes from SQLite.", choices=ELEMENTS),
    client("NormalTime", "Walk time (ms)", 12, 4, "Client interpolation."),
    client("NormalStep", "Walk step", 16, 1),
    client("ChaseTime", "Chase time (ms)", 17, 4, "Client interpolation. GX movement packets take precedence."),
    client("ChaseStep", "Chase step", 21, 1),
    *(field for i in range(3) for field in (
        client(f"AttackType{i + 1}", f"Attack {i + 1} metadata", 22 + i * 2, 1,
               "SQLite selects the attack skill."),
        client(f"AttackAnimation{i + 1}", f"Attack {i + 1} animation", 23 + i * 2, 1,
               "Selects a clip from the MON model.",
               choices=("Default attack", "Attack 1" + (" / 2 for basic attacks" if i == 0 else ""),
                        "Attack 2", "Attack 3")),
    )),
    client("AttackPlus3", "Legacy attack metadata", 28, 1),
    client("QuestItemID", "Client quest item metadata", 29, 2, "GX World controls quest drops."),
)

DB_FIELDS = (
    MobField("MobName", "Server name", "General"),
    MobField("Level", "Combat level", "General", U16, 1),
    MobField("HP", "Maximum HP", "General", U32, 1),
    MobField("MP", "Maximum MP", "General"),
    MobField("SP", "Maximum SP", "General"),
    MobField("AI", "AI code", "General", U8, note="Codes 1 and 3 acquire enemies. GX uses generic behavior for legacy FSM and Lua codes."),
    MobField("Size", "Server reach size", "General", U8),
    MobField("Attrib", "Combat element", "General", 4, choices=ELEMENTS),
    MobField("Day", "Legacy day value", "General", U8, note="GX World does not use this column.", read_only=True),
    MobField("Exp", "Experience reward", "Rewards"),
    MobField("Money1", "Base gold", "Rewards", note="Gold = base plus a random amount below the random range."),
    MobField("Money2", "Random gold range", "Rewards", note="The highest combined payout must fit in 4,294,967,295."),
    MobField("QuestItemID", "Quest drop index", "Rewards", 2147483647,
             note="0 disables it. Native ranges select item families 27, 28 and 29, with a byte-sized index."),
    *(MobField(key, label, "Combat stats", U16) for key, label in (
        ("Dex", "Dexterity"), ("Wis", "Wisdom"), ("Luc", "Luck"), ("Defense", "Defense"), ("Magic", "Magic"),
    )),
    MobField("NormalTime", "Walk time (ms)", "Movement"),
    MobField("NormalStep", "Walk step", "Movement", U16),
    MobField("ChaseTime", "Chase time (ms)", "Movement"),
    MobField("ChaseStep", "Chase step", "Movement", U16),
    MobField("ChaseRange", "Acquisition radius", "Movement", 2147483647),
    *(field for n in (1, 2, 3) for field in (
        MobField(f"AttackType{n}", "NPC skill ID", f"Attack {n}", U16, note="0 uses a basic attack."),
        MobField(f"AttackTime{n}", "Cooldown (ms)" if n < 3 else "Cooldown / shield defense", f"Attack {n}"),
        MobField(f"Attackrange{n}", "Attack range", f"Attack {n}", U16),
        MobField(f"Attack{n}", "Base damage" if n < 3 else "Recovery amount", f"Attack {n}", U16),
        MobField(f"Attackplus{n}", "Damage variance" if n < 3 else "Obelisk flag", f"Attack {n}",
                 U16 if n < 3 else 1, choices=() if n < 3 else ("Off", "On")),
        MobField(f"Attackattrib{n}", "Attack element" if n < 3 else "Faction", f"Attack {n}",
                 4 if n < 3 else 2, choices=ELEMENTS if n < 3 else ("None", "Light", "Fury")),
        MobField(f"Attackok{n}", "Activation condition", f"Attack {n}", 6, choices=CONDITIONS),
        MobField(f"Attackspecial{n}", "Legacy special code" if n < 3 else "Respawn delay", f"Attack {n}",
                 U8 if n < 3 else 15, choices=() if n < 3 else RESPAWNS, read_only=n < 3),
    )),
    *(MobField(f"ResistState{i}", f"State {i}", "Immunities", 1, choices=("Off", "Immune")) for i in range(1, 16)),
    *(MobField(f"ResistSkill{i}", f"Immune skill ID {i}", "Immunities", U16) for i in range(1, 7)),
)
CLIENT_BY_KEY = {field.key: field for field in CLIENT_FIELDS}
DB_BY_KEY = {field.key: field for field in DB_FIELDS}


class MonsterCatalog:
    def __init__(self, raw):
        self.raw = raw
        plain, self.writable = seed.decode(raw)
        reader = Cursor(plain, "Monster.SData")
        count = reader.word()
        if count > min(U16 + 1, (len(plain) - reader.position) // (BODY + 4)):
            raise ValueError("Invalid Monster.SData record count.")
        self.records = tuple((reader.string(), reader.take(BODY)) for _ in range(count))
        if reader.position != len(plain):
            raise ValueError("Unexpected trailing bytes in Monster.SData.")

    read = staticmethod(read_file)

    @classmethod
    def open(cls, path):
        return cls(cls.read(path))

    def row(self, mob_id):
        name, body = self.records[mob_id]
        return {"Name": decode_text(name), **{
            field.key: int.from_bytes(body[field.offset:field.offset + field.width], "little")
            for field in CLIENT_FIELDS if field.width
        }}

    def encode(self, edits):
        if not edits:
            return self.raw
        if not self.writable:
            raise ValueError("This Monster.SData encryption header is read-only.")
        for mob_id in edits:
            if not 0 <= mob_id < len(self.records):
                raise ValueError(f"Unknown client MobID {mob_id}.")
        output = bytearray(WORD.pack(len(self.records)))
        expected = {}
        for mob_id, (name, payload) in enumerate(self.records):
            if mob_id in edits:
                body = bytearray(payload)
                expected[mob_id] = self.row(mob_id)
                for key, value in edits[mob_id].items():
                    field = CLIENT_BY_KEY[key]
                    value = field.parse(value)
                    expected[mob_id][key] = value
                    if not field.width:
                        if value != decode_text(name):
                            name = value.encode("utf-8")
                    else:
                        start = field.offset
                        body[start:start + field.width] = value.to_bytes(field.width, "little")
                payload = bytes(body)
            output.extend(WORD.pack(len(name)))
            output.extend(name)
            output.extend(payload)
        raw = seed.encode_like(self.raw, bytes(output))
        checked = MonsterCatalog(raw)
        if any(checked.row(mob_id) != row for mob_id, row in expected.items()):
            raise ValueError("Monster.SData failed round-trip verification.")
        return raw


def mob_rows(database):
    columns = ", ".join(f"m.{quote(field.key)}" for field in DB_FIELDS)
    sql = f'SELECT m."MobID", {columns} FROM "{TABLE}" AS m WHERE m."MobID" BETWEEN 1 AND ?'
    result = database.execute(sql, (U16,)).fetchall()
    counts = Counter(row[0] for row in result)
    return {row[0]: dict(zip(DB_BY_KEY, row[1:])) for row in result if counts[row[0]] == 1}


def read_mobs(path):
    with closing(read_database(path)) as database:
        return mob_rows(database)


def drop_rate_max(slot):
    return 100 if slot < 7 else 1_000_000


def drop_rows(database):
    if not database.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=? COLLATE NOCASE", (DROPS,)).fetchone():
        return None
    rows = {}
    for mob, slot, grade, rate in database.execute(
        f'SELECT "MobID", "ItemOrder", "Grade", "DropRate" FROM "{DROPS}" WHERE "ItemOrder" BETWEEN 1 AND 9 '
        'ORDER BY "MobID", "ItemOrder", "Grade", "DropRate"',
    ):
        rows.setdefault(mob, {}).setdefault(slot, []).append((grade, rate))
    return rows


def read_monster_db(path):
    with closing(read_database(path)) as database:
        database.execute("BEGIN")
        return mob_rows(database), drop_rows(database)


def save_mobs(path, baseline, edits, backup_root, *, drop_baseline=None, drop_edits=None):
    if not edits and not drop_edits:
        raise ValueError("No SQLite edits to save.")
    if drop_edits and drop_baseline is None:
        raise ValueError("Reload the SQLite drop table before saving.")
    path = path.resolve(strict=True)
    with closing(connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)) as database, database:
        database.execute("PRAGMA trusted_schema=OFF")
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("PRAGMA synchronous=FULL")
        database.execute("BEGIN IMMEDIATE")
        assert_safe_table(database, TABLE)
        current = mob_rows(database)
        current_drops = drop_rows(database)
        if drop_edits:
            assert_safe_table(database, DROPS)
            for mob_id, slots in drop_edits.items():
                if mob_id not in current:
                    raise ValueError(f"Mob {mob_id} is missing from SQLite.")
                for slot, (grade, rate) in slots.items():
                    previous = drop_baseline.get(mob_id, {}).get(slot, [])
                    stored = current_drops.get(mob_id, {}).get(slot, [])
                    if len(stored) > 1 or stored != previous:
                        raise ValueError(f"Mob {mob_id}, drop slot {slot} changed or is duplicated in SQLite.")
                    if not (1 <= slot <= 9 and 0 <= grade <= U16 and 0 <= rate <= drop_rate_max(slot)):
                        raise ValueError(f"Invalid grade or chance for mob {mob_id}, drop slot {slot}.")
        changes = {}
        for mob_id, patch in edits.items():
            old, stored = baseline.get(mob_id), current.get(mob_id)
            if old is None or stored is None or any(old[field] != stored[field] for field in patch):
                raise ValueError(f"Mob {mob_id} changed in SQLite. Reload the SQLite data and reopen it.")
            fields = {}
            for name, value in patch.items():
                field = DB_BY_KEY[name]
                if field.read_only:
                    raise ValueError(f"{name} is read-only.")
                fields[name] = field.parse(value)
            combined = stored | fields
            if fields.keys() & {"Money1", "Money2"} and combined["Money1"] + max(0, combined["Money2"] - 1) > U32:
                raise ValueError(f"Mob {mob_id}: the highest gold payout exceeds {U32:,}.")
            changes[mob_id] = fields
        backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder = Path(mkdtemp(prefix=datetime.now().strftime("%Y%m%d-%H%M%S-Mobs-"), dir=backup_root))
        backup = folder / "rapture.sqlite"
        backup_sqlite(path, backup,
                      lambda copy: mob_rows(copy) == current and (not drop_edits or drop_rows(copy) == current_drops))
        sync_folder(folder)
        sync_folder(backup_root)
        for mob_id, fields in changes.items():
            values = ", ".join(f"{quote(name)} = ?" for name in fields)
            result = database.execute(f'UPDATE "{TABLE}" SET {values} WHERE "MobID" = ?', (*fields.values(), mob_id))
            if result.rowcount != 1:
                raise ValueError(f"Could not save mob {mob_id}.")
            current[mob_id] |= fields
        for mob_id, slots in (drop_edits or {}).items():
            for slot, (grade, rate) in slots.items():
                existing = current_drops.setdefault(mob_id, {})
                if slot in existing:
                    result = database.execute(
                        f'UPDATE "{DROPS}" SET "Grade"=?, "DropRate"=? WHERE "MobID"=? AND "ItemOrder"=?',
                        (grade, rate, mob_id, slot),
                    )
                    if result.rowcount != 1:
                        raise ValueError(f"Could not save mob {mob_id}, drop slot {slot}.")
                else:
                    database.execute(
                        f'INSERT INTO "{DROPS}" ("MobID", "ItemOrder", "Grade", "DropRate") VALUES (?, ?, ?, ?)',
                        (mob_id, slot, grade, rate),
                    )
                existing[slot] = [(grade, rate)]
        if mob_rows(database) != current:
            raise ValueError("Could not verify the SQLite mob edits.")
        if drop_edits and drop_rows(database) != current_drops:
            raise ValueError("Could not verify the SQLite drop edits.")
    return (current, current_drops), backup
