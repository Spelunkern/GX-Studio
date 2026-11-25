"""Convert supported legacy client catalogs to the GX EP5.4 layouts."""

from collections import Counter
from dataclasses import replace
from pathlib import Path

from . import seed
from .binary_sdata import binary_schema
from .cash_catalog import CashCatalog
from .catalog import BODY_SIZE, MAX_BYTES, WORD, Catalog, Cursor
from .monster_data import BODY as MONSTER_BYTES, MonsterCatalog
from .npcquest_data import GATE_FIELDS, ITEM_PAIR, NPC_FIELDS, NPCQuestCatalog, QUEST_PAYLOAD, REWARD_FIELDS, decode_quest_text
from .skill_catalog import NPCSkillCatalog, SKILL_BYTES, SKILL_RANKS, SkillCatalog

TARGET_CATALOGS = {
    "item.sdata": Catalog, "cash.sdata": CashCatalog, "monster.sdata": MonsterCatalog,
    "skill.sdata": SkillCatalog, "npcskill.sdata": NPCSkillCatalog, "npcquest.sdata": NPCQuestCatalog,
}
CONVERSION_EPISODES = ("4.5", "6.4")


def read_count(cursor, maximum):
    count = cursor.word()
    if count > maximum:
        raise ValueError(f"Invalid record count {count} in {cursor.filename}.")
    return count


def convert_items(cursor, losses):
    groups = read_count(cursor, 255)
    output = bytearray(WORD.pack(groups))
    for _ in range(groups):
        count = read_count(cursor, 255)
        output.extend(WORD.pack(count))
        for _ in range(count):
            start = cursor.position
            cursor.string()
            cursor.string()
            output.extend(cursor.data[start:cursor.position])
            # EP6.4 widened ReqLuc and Range and appended 50 bytes of additional fields.
            body = cursor.take(BODY_SIZE + 3)
            extras = cursor.take(50)
            if any(extras):
                losses["items with EP6.4-only fields removed"] += 1
            if any(body[28:30]):
                losses["item ReqLuc values capped at 65,535"] += 1
            if body[35]:
                losses["item Range values capped at 255"] += 1
            output.extend(body[:26])
            output.extend(b"\xff\xff" if any(body[28:30]) else body[26:28])
            output.extend(body[30:34])
            output.extend(b"\xff" if body[35] else body[34:35])
            output.extend(body[36:])
    return output


def convert_skills(cursor, episode, losses, *, npc):
    groups = read_count(cursor, 65535)
    output = bytearray(WORD.pack(groups))
    source_ranks = 15 if episode == "6.4" else 3
    for _ in range(groups):
        converted = {}
        for slot in range(source_ranks):
            start = cursor.position
            name = cursor.string()
            description = cursor.string()
            body_at = cursor.position
            body = cursor.take(SKILL_BYTES + (22 if episode == "6.4" else 0))
            rank = body[0]
            if npc and rank == 100 and slot == 0:
                rank = 1
                body = bytes((rank,)) + body[1:]
                losses["NPC skills with rank marker 100 normalized to rank 1"] += 1
            if rank > source_ranks:
                raise ValueError(f"Rank {rank} exceeds the EP{episode} skill layout.")
            if rank > SKILL_RANKS:
                losses["skill ranks above 9 removed"] += 1
                continue
            if not rank and slot >= SKILL_RANKS:
                continue
            if episode == "6.4":
                if rank and any(body[97:118]):
                    losses["ranks with ability slots 4–10 removed"] += 1
                # The added byte at offset 5 is a separate effect; GX reads its original toggle/effect byte.
                if rank and body[5]:
                    losses["ranks with a separate EP6 effect value removed"] += 1
                body = body[:5] + body[6:97] + body[118:]
            if rank and rank in converted:
                raise ValueError(f"Duplicate rank {rank} in a skill group.")
            if rank:
                converted[rank] = cursor.data[start:body_at] + body
            elif any(body) or name.rstrip(b"\0") or description.rstrip(b"\0"):
                losses["unused skill records omitted"] += 1
        empty = bytes(8 + SKILL_BYTES)
        for rank in range(1, SKILL_RANKS + 1):
            output.extend(converted.get(rank, empty))
    return output


def convert_monsters(cursor, losses):
    count = read_count(cursor, 65536)
    output = bytearray(WORD.pack(count))
    for _ in range(count):
        start = cursor.position
        cursor.string()
        cursor.take(MONSTER_BYTES)
        output.extend(cursor.data[start:cursor.position])
        if any(cursor.take(5)):
            losses["monsters with EP6 color overrides removed"] += 1
    return output


def convert_quests(cursor, losses, *, split_text=False):
    def skip_text():
        cursor.string()

    def skip_links():
        for _ in range(2):
            cursor.take(2 * read_count(cursor, 65536))

    npc_size = sum(codec.size for _, codec in NPC_FIELDS)
    gate_size = sum(codec.size for _, codec in GATE_FIELDS[:-1])
    output = bytearray()
    empty = bytes(4)
    for family in range(1, 14):
        count = read_count(cursor, 65535)
        output.extend(WORD.pack(count))
        for _ in range(count):
            output.extend(cursor.take(npc_size + (family == 1)))
            for _ in range(2):
                text = b"" if split_text else cursor.string()
                output.extend(WORD.pack(len(text)) + text)
            if family == 1:
                start = cursor.position
                cursor.take(ITEM_PAIR.size * read_count(cursor, 65536))
                output.extend(cursor.data[start:cursor.position])
            if family == 2:
                for _ in range(3):
                    output.extend(cursor.take(gate_size))
                    text = b"" if split_text else cursor.string()
                    output.extend(WORD.pack(len(text)) + text)
                    output.extend(cursor.take(GATE_FIELDS[-1][1].size))
            start = cursor.position
            skip_links()
            output.extend(cursor.data[start:cursor.position])
    start = cursor.position
    for _ in range(256 * 256):
        skip_links()
    quests = read_count(cursor, 65535)
    output.extend(cursor.data[start:cursor.position])
    reward_size = sum(codec.size for _, codec in REWARD_FIELDS)
    requirements_size = sum(codec.size for _, codec in QUEST_PAYLOAD) - 3 * reward_size
    for _ in range(quests):
        output.extend(cursor.take(2))
        if split_text:
            output.extend(empty * 2)
        else:
            start = cursor.position
            skip_text()
            skip_text()
            output.extend(cursor.data[start:cursor.position])
        output.extend(cursor.take(requirements_size))
        completions = []
        for result in range(6):
            first_reward = cursor.take(reward_size - 2)
            extra_items = cursor.take(7)
            next_quest = cursor.take(2)
            completion = b"" if split_text else cursor.string()
            if result < 3:
                output.extend(first_reward + next_quest)
                completions.append(WORD.pack(len(completion)) + completion)
                if extra_items[0] > 1 or any(extra_items[1:]):
                    losses["quest reward options reduced to one item of quantity one"] += 1
            elif any(first_reward + extra_items + next_quest) or completion.rstrip(b"\0"):
                losses["quest reward options 4–6 removed"] += 1
        if split_text:
            output.extend(empty * 4)
        else:
            start = cursor.position
            for _ in range(4):
                skip_text()
            output.extend(cursor.data[start:cursor.position])
        for completion in completions:
            output.extend(completion)
    return output


def convert_catalog(name: str, raw: bytes, episode: str):
    if episode not in CONVERSION_EPISODES:
        raise ValueError("GX conversion supports source episodes 4.5 and 6.4.")
    filename = Path(name).name.casefold()
    parser = TARGET_CATALOGS.get(filename)
    if parser is None:
        raise ValueError(f"{name} has no GX EP5.4 catalog converter.")
    plain, _ = seed.decode(raw)
    losses = Counter()

    # Clients can ship a mixture of formats. Preserve an already compatible catalog verbatim after decryption.
    try:
        compatible = parser(plain)
        if isinstance(compatible, SkillCatalog) and any(rank > SKILL_RANKS for _, rank in compatible.records):
            raise ValueError("The input contains ranks outside GX EP5.4.")
    except ValueError:
        cursor = Cursor(plain, name)
        if filename in ("skill.sdata", "npcskill.sdata"):
            output = convert_skills(cursor, episode, losses, npc=filename == "npcskill.sdata")
        elif episode == "6.4" and filename == "item.sdata":
            output = convert_items(cursor, losses)
        elif episode == "6.4" and filename == "monster.sdata":
            output = convert_monsters(cursor, losses)
        elif episode == "6.4" and filename == "npcquest.sdata":
            output = convert_quests(cursor, losses)
        else:
            raise ValueError(f"{name} does not match the supported EP{episode} layout.") from None
        if cursor.position != len(plain):
            raise ValueError(f"{name} has trailing bytes for the EP{episode} layout.")
        plain = bytes(output)
        if len(plain) > MAX_BYTES:
            raise ValueError(f"The converted {name} exceeds the GX catalog size limit.")
        parser(plain)
    return plain, losses


def inspect_catalog(name, raw):
    """Return a GX-compatible view plus the layout's distinguishing episode."""
    parser = TARGET_CATALOGS[name.casefold()]
    plain, _ = seed.decode(raw)
    try:
        catalog = parser(raw)
        if isinstance(catalog, SkillCatalog):
            if any(rank > SKILL_RANKS for _, rank in catalog.records):
                raise ValueError("Invalid EP5.4 skill rank.")
            return catalog, "5.4"
        return catalog, None
    except ValueError as native_error:
        for episode in CONVERSION_EPISODES:
            try:
                normalized, _ = convert_catalog(name, plain, episode)
            except ValueError:
                continue
            if normalized != plain:
                catalog = parser(normalized)
                catalog.writable = False
                catalog.legacy_preview = True
                return catalog, episode
        if parser is NPCQuestCatalog:
            try:
                cursor = Cursor(plain, name)
                normalized = convert_quests(cursor, Counter(), split_text=True)
                if cursor.position != len(plain):
                    raise ValueError("Trailing bytes in EP8 NpcQuest.")
                catalog = parser(bytes(normalized))
            except ValueError:
                pass
            else:
                catalog.writable = False
                catalog.legacy_preview = True
                return catalog, "8"
        raise ValueError(f"{name} does not match a supported legacy layout. {native_error}") from native_error


def attach_ep8_quest_translation(catalog, filename, raw):
    plain, _ = seed.decode(raw, binary=True)
    cursor = Cursor(plain, filename)
    npcs = {}
    for group in range(1, 14):
        entries = [(key, npc) for key, npc in catalog.data.npcs.items() if key[0] == group]
        if cursor.word() != len(entries):
            raise ValueError(f"{filename} does not match the NPC groups.")
        for key, npc in entries:
            name, welcome = (decode_quest_text(cursor.string()) for _ in range(2))
            gates = tuple(replace(gate, label=decode_quest_text(cursor.string())) for gate in npc.gates)
            npcs[key] = replace(npc, name=name, welcome=welcome, gates=gates)
    if cursor.word() != len(catalog.data.quests):
        raise ValueError(f"{filename} does not match the quest count.")
    quests = {}
    for key, quest in catalog.data.quests.items():
        name, summary, *texts = (decode_quest_text(cursor.string()) for _ in range(12))
        quests[key] = replace(quest, name=name, texts=(summary, *texts[6:], *texts[:3]))
    padding = plain[cursor.position:]
    if len(padding) > 15 or any(padding):
        raise ValueError(f"Unexpected bytes after the translations in {filename}.")
    catalog.data = replace(catalog.data, npcs=npcs, quests=quests)


def inspect_source(source):
    episodes = {}
    candidates = 0
    ep7_interface = False
    for entry in source.entries:
        name = entry.path.name.casefold()
        ep7_interface |= any(part.casefold() == "intf_epi7" for part in entry.path.parts[:-1])
        if binary_schema(name):
            episodes[entry.path] = "8"
        elif name in TARGET_CATALOGS:
            candidates += 1
            try:
                _, episode = inspect_catalog(name, source.read_catalog(entry, MAX_BYTES))
            except ValueError:
                continue
            episodes[entry.path] = episode
    if candidates and not episodes:
        raise ValueError("The client catalogs do not match EP4.5, EP5.4 or EP6.4.")
    known = {episode for episode in episodes.values() if episode is not None}
    variants = known - {"5.4"}
    if "8" in known:
        episode = "8"
    elif ep7_interface and "4.5" not in known:
        episode = "7"
    else:
        episode = next(iter(variants)) if len(variants) == 1 else "5.4" if known == {"5.4"} else None
    return episodes, episode
