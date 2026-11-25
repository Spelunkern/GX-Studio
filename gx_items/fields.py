from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    label: str
    section: str
    offset: int
    size: int = 1
    column: str = ""
    sql_max: int | None = None
    signed: bool = False

    def numeric_limits(self) -> tuple[int, int]:
        bits = 8 * self.size - int(self.signed)
        return (-(1 << bits) if self.signed else 0,
                self.sql_max if self.sql_max is not None else (1 << bits) - 1)

    def parse(self, value: str) -> str | int:
        if not self.size:
            try:
                encoded = value.encode("utf-8", errors="strict")
            except UnicodeEncodeError as error:
                raise ValueError("This text contains characters that cannot be encoded in UTF-8.") from error
            if "\0" in value or len(encoded) > 65535:
                raise ValueError("Enter UTF-8 text up to 65,535 bytes, without NUL characters.")
            return value
        minimum, maximum = self.numeric_limits()
        value = value.strip()
        digits = value[1:] if self.signed and value.startswith("-") else value
        if not digits.isascii() or not digits.isdecimal() or len(digits) > 10:
            raise ValueError(f"Enter an integer from {minimum:,} to {maximum:,}.")
        number = int(value)
        if not minimum <= number <= maximum:
            raise ValueError(f"Enter an integer from {minimum:,} to {maximum:,}.")
        return number

    def sql_value(self, value: str | int) -> str | int:
        if self.name == "Count" and isinstance(value, int) and value > 1:
            return 255
        return value


IDENTITY = "Identity"
VISUAL = "Appearance"
REQUIREMENTS = "Requirements"
COMBAT = "Combat"
BONUSES = "Bonuses"
OTHER = "Shop and other"
CLASSES = (
    ("Fighter", "Attackfighter"), ("Defender", "Defensefighter"),
    ("Rogue", "Patrolrogue"), ("Archer", "Shootrogue"),
    ("Mage", "Attackmage"), ("Priest", "Defensemage"),
)
STATS = ("Str", "Dex", "Rec", "Int", "Wis", "Luc")
COMBAT_WORDS = (
    ("Base attack", "Attack", "Effect1"), ("Attack variance", "AttackAdd", "Effect2"),
    ("Defense", "Def", "Effect3"), ("Magic resistance", "Resist", "Effect4"),
)
BONUS_WORDS = (
    ("HP", "RecoveryHP", "ConstHP"), ("SP", "RecoverySP", "ConstSP"),
    ("MP", "RecoveryMP", "ConstMP"),
    *((stat.upper(), stat, "Const" + stat) for stat in STATS),
)
FIELDS = (
    Field("Name", "Name", IDENTITY, 0, 0, "ItemName"),
    Field("Description", "Description", IDENTITY, 1, 0),
    Field("Model", "Model", VISUAL, 2),
    Field("Icon", "Icon", VISUAL, 3),
    Field("MinLevel", "Required level", REQUIREMENTS, 4, 2, "Reqlevel"),
    Field("Country", "Faction", REQUIREMENTS, 6, 1, "Country"),
    *(Field(name, label, REQUIREMENTS, 7 + i, 1, name) for i, (label, name) in enumerate(CLASSES)),
    Field("Grow", "Grow flag", REQUIREMENTS, 13, 1, "Grow"),
    *(Field("Req" + stat, "Required " + stat.upper(), REQUIREMENTS, 16 + i * 2, 2,
            "Reqluc" if stat == "Luc" else "Req" + stat, None if stat == "Luc" else 32767)
      for i, stat in enumerate(STATS)),
    Field("ReqVG", "VG parameter", REQUIREMENTS, 28, 2, "ReqVg", 32767),
    Field("ReqOG", "Character restrictions", REQUIREMENTS, 30, 1, "ReqOg"),
    Field("ReqIg", "IG parameter", REQUIREMENTS, 31, 1, "ReqIg"),
    Field("Range", "Range / low byte of skill ID", COMBAT, 32, 1, "Range"),
    Field("AttackTime", "Attack tier / skill level", COMBAT, 33, 1, "AttackTime"),
    Field("Attribute", "Element", COMBAT, 34, 1, "Attrib"),
    Field("Special", "Special effect", COMBAT, 35, 1, "Special"),
    Field("Slot", "Sockets", COMBAT, 36, 1, "Slot"),
    Field("Quality", "Durability", COMBAT, 37, 2, "Quality"),
    *(Field(name, label, COMBAT, 39 + i * 2, 2, column, 32767)
      for i, (label, name, column) in enumerate(COMBAT_WORDS)),
    *(Field(name, label, BONUSES, 47 + i * 2, 2, column, 32767)
      for i, (label, name, column) in enumerate(BONUS_WORDS)),
    Field("Speed", "Movement speed", BONUSES, 65, 1, "Speed"),
    Field("Exp", "Flat damage absorption", BONUSES, 66, 1, "Exp"),
    Field("Type2", "Client flag 2", OTHER, 14),
    Field("Type3", "Client flag 3", OTHER, 15),
    Field("BuyPrice", "Buy price", OTHER, 67, 4, "Buy", 2147483647),
    Field("SellPrice", "Sell price", OTHER, 71, 4, "Sell", 2147483647),
    Field("Grade", "Loot group", OTHER, 75, 2, "Grade"),
    Field("Drop", "Loot limit selector", OTHER, 77, 2, "Drop"),
    Field("Server", "Server flag", OTHER, 79, 1, "Server"),
    Field("Count", "Native stack flag", OTHER, 80, 1, "Count"),
)
FIELDS_BY_NAME = {field.name: field for field in FIELDS}
