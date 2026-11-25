from dataclasses import dataclass
from pathlib import Path

from . import seed
from .catalog import Cursor, MAX_BYTES, Scalar, WORD, decode_text, read_file
from .fields import CLASSES, Field
from .npc_skills import SKILL_BYTES, SKILL_RANKS

APPEARANCE = "Appearance"
REQUIREMENTS = "Requirements"
CAST = "Cast and Target"
DAMAGE = "Damage and Healing"
EFFECTS = "Effects"
LEGACY = "Legacy"
SkillKey = tuple[int, int]
SkillEdits = dict[SkillKey, dict[str, Scalar]]


def skill_field(name, label, section, offset, size=1, *, signed=False, client=False):
    return Field(name, label, section, offset, size, "" if client else name, signed=signed)


SKILL_FIELDS = (
    Field("Name", "Name", APPEARANCE, 0, 0, "SkillName"),
    Field("Description", "Description", APPEARANCE, 1, 0),
    skill_field("Icon", "Icon", APPEARANCE, 1, 2, client=True),
    skill_field("Animation", "Animation", APPEARANCE, 3, client=True),
    skill_field("VisualCode", "Visual effect", APPEARANCE, 4, 2, client=True),
    skill_field("Sound", "Sound", APPEARANCE, 6, 2, client=True),
    skill_field("ReqLevel", "Required character level", REQUIREMENTS, 8, 2),
    skill_field("Country", "Faction", REQUIREMENTS, 10),
    *(skill_field(key, label, REQUIREMENTS, 11 + i) for i, (label, key) in enumerate(CLASSES)),
    skill_field("Grow", "Legacy growth", LEGACY, 17),
    skill_field("SkillPoint", "Skill points", REQUIREMENTS, 18),
    skill_field("TypeShow", "Category", REQUIREMENTS, 19),
    skill_field("TypeAttack", "Attack type", CAST, 20),
    skill_field("TypeEffect", "Effect", EFFECTS, 21),
    skill_field("TypeDetail", "Effect detail", EFFECTS, 22, 2),
    *(skill_field(f"NeedWeapon{i + 1}", f"Weapon {i + 1}", REQUIREMENTS, 24 + i) for i in range(15)),
    skill_field("Shield", "Shield", REQUIREMENTS, 39),
    skill_field("SP", "SP cost", CAST, 40, 2),
    skill_field("MP", "MP cost", CAST, 42, 2),
    skill_field("ReadyTime", "Cast time (0.25 s units)", CAST, 44),
    skill_field("ResetTime", "Cooldown (seconds)", CAST, 45, 2),
    skill_field("AttackRange", "Cast range", CAST, 47),
    skill_field("StateType", "Status type", EFFECTS, 48),
    skill_field("AttrType", "Element", DAMAGE, 49),
    skill_field("Disable", "Legacy disable flag", LEGACY, 50, 2, signed=True),
    skill_field("PrevSkillID", "Linked cooldown skill ID", REQUIREMENTS, 52, 2),
    skill_field("SuccessType", "Success type", CAST, 54),
    skill_field("SuccessValue", "Success value", CAST, 55),
    skill_field("TargetType", "Target", CAST, 56),
    skill_field("ApplyRange", "Effect radius", CAST, 57),
    skill_field("MultiAttack", "Multi-hit", CAST, 58),
    skill_field("KeepTime", "Duration", EFFECTS, 59, 2),
    skill_field("Weapon1", "Passive weapon 1", EFFECTS, 61),
    skill_field("Weapon2", "Passive weapon 2", EFFECTS, 62),
    skill_field("Weaponvalue", "Passive weapon bonus", EFFECTS, 63),
    skill_field("Bag", "Bag", EFFECTS, 64),
    skill_field("Arrow", "Aggro", EFFECTS, 65, 2),
    skill_field("DamageType", "Damage type", DAMAGE, 67),
    skill_field("TimeDamageType", "Periodic damage type", DAMAGE, 74),
    *(skill_field(f"{key}{pool}", f"{label} {pool}", DAMAGE, offset + i * 2, 2, signed=signed)
      for key, label, offset, signed in (("Damage", "Damage", 68, False),
                                          ("TimeDamage", "Periodic damage", 75, False),
                                          ("AddDamage", "Extra damage", 81, False),
                                          ("Heal", "Healing", 96, True),
                                          ("TimeHeal", "Periodic healing", 102, True))
      for i, pool in enumerate(("HP", "SP", "MP"))),
    *(skill_field(f"Ability{kind}{i + 1}", f"Ability {i + 1} {label}", EFFECTS, offset + i * 3, size, signed=signed)
      for i in range(3) for kind, label, offset, size, signed in (("Type", "type", 87, 1, False),
                                                                   ("Value", "value", 88, 2, True))),
    skill_field("DefenceType", "Defense type", EFFECTS, 108),
    skill_field("DefenceValue", "Defense value", EFFECTS, 109),
    skill_field("LimitHP", "HP threshold", EFFECTS, 110),
    skill_field("FixRange", "Duration mode", EFFECTS, 111),
    skill_field("ChangeType", "Transformation type", EFFECTS, 112, 2),
    skill_field("ChangeLevel", "Transformation value", EFFECTS, 114, 2),
)
SKILL_FIELDS_BY_NAME = {field.name: field for field in SKILL_FIELDS}
SKILL_SQL_FIELDS = tuple(field for field in SKILL_FIELDS if field.column)


@dataclass(frozen=True, slots=True)
class SkillRecord:
    start: int
    description: int
    body: int

    @property
    def end(self):
        return self.body + SKILL_BYTES


class SkillCatalog:
    filename = "Skill.SData"

    def __init__(self, raw: bytes):
        self.raw = raw
        self.plain, self.writable = seed.decode(raw)
        cursor = Cursor(self.plain)
        groups = cursor.word()
        if not 1 <= groups <= min(65535, (len(self.plain) - 4) // (SKILL_RANKS * (SKILL_BYTES + 8))):
            raise ValueError(f"Invalid group count in {self.filename}.")
        self.records: dict[SkillKey, SkillRecord] = {}
        for skill_id in range(1, groups + 1):
            for _ in range(SKILL_RANKS):
                start = cursor.position
                cursor.string()
                description = cursor.position
                cursor.string()
                body = cursor.position
                rank = cursor.take(SKILL_BYTES)[0]
                if not rank:
                    continue
                key = skill_id, rank
                if key in self.records:
                    raise ValueError(f"Duplicate skill {skill_id} rank {rank}.")
                self.records[key] = SkillRecord(start, description, body)
        if cursor.position != len(self.plain):
            raise ValueError(f"Unexpected bytes after the {self.filename} catalog.")

    @classmethod
    def open(cls, path: Path):
        return cls(read_file(path))

    def value(self, key: SkillKey, field: Field) -> Scalar:
        record = self.records[key]
        if field.size:
            start = record.body + field.offset
            return int.from_bytes(self.plain[start:start + field.size], "little", signed=field.signed)
        start, end = (record.start, record.description) if not field.offset else (record.description, record.body)
        return decode_text(self.plain[start + WORD.size:end])

    def encode(self, edits: SkillEdits) -> bytes:
        if not edits:
            return self.raw
        if not self.writable:
            raise ValueError("This encrypted file is read-only.")
        output = bytearray()
        offset = 0
        verified = {}
        for key in sorted(edits, key=lambda key: self.records[key].start):
            record = self.records[key]
            output.extend(self.plain[offset:record.start])
            body = bytearray(self.plain[record.body:record.end])
            strings = [self.plain[record.start:record.description], self.plain[record.description:record.body]]
            for name, raw in edits[key].items():
                field = SKILL_FIELDS_BY_NAME[name]
                value = field.parse(str(raw))
                if value == self.value(key, field):
                    continue
                if isinstance(value, int):
                    body[field.offset:field.offset + field.size] = value.to_bytes(field.size, "little", signed=field.signed)
                else:
                    _, nul, suffix = strings[field.offset][WORD.size:].partition(b"\0")
                    data = value.encode("utf-8") + nul + suffix
                    strings[field.offset] = WORD.pack(len(data)) + data
                verified[(key, field)] = value
            output.extend(b"".join((*strings, body)))
            offset = record.end
        if not verified:
            return self.raw
        output.extend(self.plain[offset:])
        raw = seed.encode_like(self.raw, bytes(output))
        if len(raw) > MAX_BYTES:
            raise ValueError(f"The {self.filename} catalog exceeds 16 MiB.")
        parsed = type(self)(raw)
        if parsed.records.keys() != self.records.keys() or any(parsed.value(key, field) != value
                                                              for (key, field), value in verified.items()):
            raise ValueError(f"{self.filename} failed round-trip verification.")
        return raw

    def sql_rows(self):
        return {key: tuple(self.value(key, field) for field in SKILL_SQL_FIELDS) for key in self.records}


class NPCSkillCatalog(SkillCatalog):
    filename = "NpcSkill.SData"

    def __init__(self, raw: bytes):
        super().__init__(raw)
        for skill_id, rank in self.records:
            if rank > SKILL_RANKS:
                raise ValueError(f"NPC skill {skill_id} has invalid rank {rank}. GX stores ranks 1–{SKILL_RANKS}.")
