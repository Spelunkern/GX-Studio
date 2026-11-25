from dataclasses import dataclass, field, replace
from math import isfinite
from pathlib import Path
from struct import Struct

from . import seed
from .catalog import MAX_BYTES, Cursor, WORD, decode_text, read_file

U8, U16, U32, I16, I32, FLOAT = (Struct('<' + code) for code in ('B', 'H', 'I', 'h', 'i', 'f'))
NPC_FIELDS = (("NPCType", U8), ("NPCTypeID", U16), ("Model", I32), ("MoveDistance", I32),
              ("MoveSpeed", I32), ("Faction", I32))
GATE_FIELDS = (("MapID", U16), ("X", FLOAT), ("Y", FLOAT), ("Z", FLOAT), ("Money", U32))
JOBS = ("AttackFighter", "DefenseFighter", "PatrolRogue", "ShooterRogue", "AttackMage", "DefenseMage")
QUEST_REQUIREMENTS = (
    ("MinLevel", U16), ("MaxLevel", U16), ("Faction", U8), ("Mode", U8), ("Male", U8), ("Female", U8),
    *((name, U8) for name in JOBS), ("HG", U16), ("VG", I16), ("CG", U8), ("OG", U8), ("IG", U8),
    ("PrevQuest", U16), ("RequireParty", U8), *(("Party" + name, U8) for name in JOBS),
    *((name, U32) for name in ("MinMinutes", "MaxMinutes", "TickStart", "TickKeep", "TickReceive")),
)
QUEST_TRIGGER = (("StartType", U8), ("StartNPCType", U8), ("StartNPCID", U16), ("StartItemType", U8), ("StartItemID", U8))
QUEST_END = (("EndType", U8), ("EndNPCType", U8), ("EndNPCID", U16))
QUEST_COUNTERS = (("PVPKills", U8), ("Mob1ID", U16), ("Mob1Count", U8), ("Mob2ID", U16), ("Mob2Count", U8))
QUEST_RESULT = (("ResultType", U8), ("UserSelect", U8))
REWARD_FIELDS = (
    ("NeedMobID", U16), ("NeedMobCount", U8), ("NeedItemID", U8), ("NeedItemCount", U8), ("NeedTime", U32),
    ("NeedHG", U16), ("NeedVG", I16), ("NeedOG", U8), ("XP", U32), ("Gold", U32), ("ItemType", U8),
    ("ItemTypeID", U8), ("NextQuest", U16),
)
QUEST_TEXTS = ("Summary", "Complete", "Response 1", "Response 2", "Offer", "Welcome", "Incomplete", "Response 4")
Pair = tuple[int, int]
Links = tuple[tuple[int, ...], tuple[int, ...]]
EMPTY_LINKS: Links = ((), ())
EMPTY_FAMILY = bytes(256 * 8)
ITEM_PAIR = Struct("<BB")


def quest_items(prefix: str):
    return tuple((f"{prefix}Item{index}{field}", U8) for index in (1, 2, 3) for field in ("Type", "ID", "Count"))


QUEST_FIELDS = (("EmbeddedID", U16), *QUEST_REQUIREMENTS, *QUEST_TRIGGER, *quest_items("Start"), *QUEST_END,
                *quest_items("Farm"), *QUEST_COUNTERS, *QUEST_RESULT,
                *((f"Reward{i}{name}", codec) for i in (1, 2, 3) for name, codec in REWARD_FIELDS))
QUEST_PAYLOAD = QUEST_FIELDS[1:]


def decode_quest_text(raw: bytes) -> str:
    visible = raw.split(b"\0", 1)[0]
    try:
        return visible.decode("utf-8")
    except UnicodeDecodeError:
        if any(a >= 128 and b >= 128 for a, b in zip(visible, visible[1:])):
            try:
                return visible.decode("cp949")
            except UnicodeDecodeError:
                pass
        return decode_text(visible)


def text_bytes(value: str, before: bytes) -> bytes:
    if value == decode_quest_text(before):
        return before
    data = value.encode("utf-8")
    _, nul, suffix = before.partition(b"\0")
    if len(data) + len(suffix) + len(nul) > MAX_BYTES or b"\0" in data:
        raise ValueError("The text exceeds the GX data size limit or contains a NUL character.")
    return data + nul + suffix


def read_fields(cursor, fields):
    return {name: codec.unpack(cursor.take(codec.size))[0] for name, codec in fields}


def empty_fields(fields):
    return {name: 0.0 if codec is FLOAT else 0 for name, codec in fields}


@dataclass(frozen=True)
class Gate:
    fields: dict = field(default_factory=lambda: empty_fields(GATE_FIELDS))
    label: str = ""


@dataclass(frozen=True)
class NPC:
    fields: dict
    name: str = ""
    welcome: str = ""
    products: tuple[Pair, ...] = ()
    gates: tuple[Gate, ...] = ()
    links: Links = EMPTY_LINKS


@dataclass(frozen=True)
class Quest:
    fields: dict
    name: str = ""
    texts: tuple[str, ...] = ("",) * len(QUEST_TEXTS)


@dataclass(frozen=True)
class QuestData:
    npcs: dict[Pair, NPC]
    quests: dict[int, Quest]
    item_links: dict[Pair, Links]

    def draft(self):
        return QuestData(self.npcs.copy(), self.quests.copy(), self.item_links.copy())

    def shared(self):
        return QuestData({key: replace(npc, welcome="", gates=tuple(replace(gate, label="") for gate in npc.gates))
                          for key, npc in self.npcs.items()},
                         {key: replace(quest, texts=("",) * len(QUEST_TEXTS)) for key, quest in self.quests.items()},
                         self.item_links.copy())


class NPCQuestCatalog:
    filename = "NpcQuest.SData"

    def __init__(self, raw: bytes):
        self.raw = raw
        self.plain, self.writable = seed.decode(raw)
        self.strings = {}
        self.data = self.parse()

    @classmethod
    def open(cls, path: Path):
        return cls(read_file(path))

    def parse(self):
        cursor = Cursor(self.plain, self.filename)
        data = QuestData({}, {}, {})

        def count(maximum):
            value = cursor.word()
            if value > maximum:
                raise ValueError(f"Invalid NpcQuest count {value} at byte {cursor.position}.")
            return value

        def text(key):
            raw = self.strings[key] = cursor.string()
            return decode_quest_text(raw)

        def links():
            start = tuple(row for row, in U16.iter_unpack(cursor.take(2 * count(65536))))
            end = tuple(row for row, in U16.iter_unpack(cursor.take(2 * count(65536))))
            return start, end

        for group in range(1, 14):
            for row in range(1, count(65535) + 1):
                key = group, row
                values = read_fields(cursor, NPC_FIELDS[:2])
                if group == 1:
                    values.update(read_fields(cursor, (("MerchantType", U8),)))
                values.update(read_fields(cursor, NPC_FIELDS[2:]))
                name, welcome = text(("npc", key, "name")), text(("npc", key, "welcome"))
                products = tuple(ITEM_PAIR.iter_unpack(cursor.take(2 * count(65536)))) if group == 1 else ()
                gates = []
                if group == 2:
                    for slot in range(1, 4):
                        fields = read_fields(cursor, GATE_FIELDS[:-1])
                        label = text(("gate", key, slot))
                        fields.update(read_fields(cursor, GATE_FIELDS[-1:]))
                        gates.append(Gate(fields, label))
                data.npcs[key] = NPC(values, name, welcome, products, tuple(gates), links())

        for family in range(256):
            if self.plain.startswith(EMPTY_FAMILY, cursor.position):
                cursor.position += len(EMPTY_FAMILY)
                continue
            for type_id in range(256):
                pair = links()
                if pair != EMPTY_LINKS:
                    data.item_links[family, type_id] = pair

        for row in range(1, count(65535) + 1):
            values = read_fields(cursor, QUEST_FIELDS[:1])
            name = text(("quest", row, "name"))
            summary = text(("quest", row, QUEST_TEXTS[0]))
            values.update(read_fields(cursor, QUEST_PAYLOAD))
            texts = (summary, *(text(("quest", row, label)) for label in QUEST_TEXTS[1:]))
            data.quests[row] = Quest(values, name, texts)
        if cursor.position != len(self.plain):
            raise ValueError("Unexpected bytes after the NpcQuest catalog.")
        return data

    def encode(self, data: QuestData):
        if data == self.data:
            return self.raw
        if not self.writable:
            raise ValueError("This encrypted file is read-only.")
        output = bytearray()

        def fields(values, schema):
            for name, codec in schema:
                value = values[name]
                if codec is FLOAT and not isfinite(value):
                    raise ValueError(f"{name} must be a finite coordinate.")
                output.extend(codec.pack(value))

        def text(key, value):
            raw = text_bytes(value, self.strings.get(key, b""))
            output.extend(WORD.pack(len(raw)) + raw)

        def links(pair):
            for entries in pair:
                output.extend(WORD.pack(len(entries)))
                for value in entries:
                    output.extend(U16.pack(value))

        for group in range(1, 14):
            keys = sorted(key for key in data.npcs if key[0] == group)
            if keys != [(group, row) for row in range(1, len(keys) + 1)]:
                raise ValueError(f"NPC group {group} must keep contiguous row IDs.")
            output.extend(WORD.pack(len(keys)))
            for key in keys:
                npc = data.npcs[key]
                fields(npc.fields, NPC_FIELDS[:2])
                if group == 1:
                    fields(npc.fields, (("MerchantType", U8),))
                fields(npc.fields, NPC_FIELDS[2:])
                text(("npc", key, "name"), npc.name)
                text(("npc", key, "welcome"), npc.welcome)
                if group == 1:
                    output.extend(WORD.pack(len(npc.products)))
                    for product in npc.products:
                        output.extend(bytes(product))
                if group == 2:
                    if len(npc.gates) != 3:
                        raise ValueError("Gatekeepers require three native destination slots.")
                    for slot, gate in enumerate(npc.gates, 1):
                        fields(gate.fields, GATE_FIELDS[:-1])
                        text(("gate", key, slot), gate.label)
                        fields(gate.fields, GATE_FIELDS[-1:])
                links(npc.links)

        item_families = {key[0] for key in data.item_links}
        for family in range(256):
            if family not in item_families:
                output.extend(EMPTY_FAMILY)
                continue
            for type_id in range(256):
                links(data.item_links.get((family, type_id), EMPTY_LINKS))

        if sorted(data.quests) != list(range(1, len(data.quests) + 1)):
            raise ValueError("Quest row IDs must stay contiguous.")
        output.extend(WORD.pack(len(data.quests)))
        for row, quest in sorted(data.quests.items()):
            fields(quest.fields, QUEST_FIELDS[:1])
            text(("quest", row, "name"), quest.name)
            text(("quest", row, QUEST_TEXTS[0]), quest.texts[0])
            fields(quest.fields, QUEST_PAYLOAD)
            for name, value in zip(QUEST_TEXTS[1:], quest.texts[1:]):
                text(("quest", row, name), value)

        raw = seed.encode_like(self.raw, bytes(output))
        if len(raw) > MAX_BYTES or NPCQuestCatalog(raw).data != data:
            raise ValueError("NpcQuest failed round-trip verification.")
        return raw


def changed_fields(origin, edited, existing):
    return existing | {name: value for name, value in edited.items() if value != origin[name]}


def changed_links(origin, edited, existing):
    return tuple(new if old != new else current for old, new, current in zip(origin, edited, existing))


def server_catalog(before: QuestData, after: QuestData, server: QuestData, *, full: bool) -> QuestData:
    """Apply client rules while retaining each server record's dialogue bytes."""
    old = before.shared()
    new = after.shared()
    if full:
        result = new
    else:
        result = server.shared()
        for origin, desired, target, title in ((old.npcs, new.npcs, result.npcs, "NPC"),
                                               (old.quests, new.quests, result.quests, "quest"),
                                               (old.item_links, new.item_links, result.item_links, "item link")):
            for key in origin.keys() - desired.keys():
                if key in target:
                    del target[key]
            for key, edited in desired.items():
                original = origin.get(key)
                if original == edited:
                    continue
                existing = target.get(key)
                if original is None and existing is None:
                    target[key] = edited
                elif original is None or existing is None:
                    raise ValueError(f"{title.title()} {key} differs between the files. Use Install to synchronize all rules.")
                elif isinstance(edited, (NPC, Quest)):
                    updates = {"fields": changed_fields(original.fields, edited.fields, existing.fields)}
                    if edited.name != original.name:
                        updates["name"] = edited.name
                    if isinstance(edited, NPC):
                        if edited.products != original.products:
                            updates["products"] = edited.products
                        updates["gates"] = tuple(
                            replace(current, fields=changed_fields(old_gate.fields, new_gate.fields, current.fields))
                            for old_gate, new_gate, current in zip(original.gates, edited.gates, existing.gates, strict=True)
                        )
                        updates["links"] = changed_links(original.links, edited.links, existing.links)
                    target[key] = replace(existing, **updates)
                else:
                    target[key] = changed_links(original, edited, existing)
    for key, npc in tuple(result.npcs.items()):
        if existing := server.npcs.get(key):
            gates = tuple(replace(gate, label=existing.gates[i].label if i < len(existing.gates) else "")
                          for i, gate in enumerate(npc.gates))
            result.npcs[key] = replace(npc, welcome=existing.welcome, gates=gates)
    for key, quest in tuple(result.quests.items()):
        if existing := server.quests.get(key):
            result.quests[key] = replace(quest, texts=existing.texts)
    return result
