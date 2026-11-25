from .equipment_models import ARMOR_TYPES, CAPE_TYPES, WEAPON_TYPES
from .fields import CLASSES

StatRow = tuple[tuple[str, str], ...]
REQUIREMENTS = (("Level", "MinLevel"), ("Faction", "Country"))
STATS = (
    (("STR", "Str"), ("DEX", "Dex"), ("REC", "Rec")),
    (("LUC", "Luc"), ("INT", "Int"), ("WIS", "Wis")),
)
POOLS = (("HP", "RecoveryHP"), ("SP", "RecoverySP"), ("MP", "RecoveryMP"))
PRICES = (("Buy", "BuyPrice"), ("Sell", "SellPrice"), ("Restrict.", "ReqOG"))
CRAFT = (("OJ rolls", "Server"), ("OJ max", "ReqWis"), ("Drop group", "Grade"))
ACCESSORIES = (22, 23, 37, 38, 40, 41)
HIDDEN_EQUIPMENT_FIELDS = frozenset(("Count", "Drop", "Quality", "Special"))
ATTACK_SPEED_NAMES = (
    "None", "Ext. Slow", "Very Slow", "Slow", "A Bit Slow",
    "Normal", "A Bit Fast", "Fast", "Very Fast", "Ext. Fast",
)


def profile(family: int) -> str:
    if family in WEAPON_TYPES:
        return "shield" if WEAPON_TYPES[family] == 19 else "weapon"
    if family in ARMOR_TYPES:
        return "chest" if ARMOR_TYPES[family] == "upper" else "armor"
    if family in CAPE_TYPES:
        return "cape"
    if family in ACCESSORIES:
        return "accessory"
    if family == 42:
        return "mount"
    return ""


def rows_for(family: int) -> tuple[tuple[str, tuple[StatRow, ...]], ...]:
    kind = profile(family)
    if not kind:
        return ()
    if kind == "mount":
        return (
            ("Characteristics", ((("Seats", "AttackTime"), ("Speed", "Speed"), ("Cooldown", "ReqIg")),)),
            ("Prices", (PRICES,)),
        )
    if kind == "weapon":
        combat = (
            (("Attack", "Attack"), ("up to", "AttackAdd"), ("Elem.", "Attribute")),
            (("Range", "Range"), ("Atk speed", "AttackTime"), ("Move", "Speed")),
            (("Absorb", "Exp"), ("Slots", "Slot")),
        )
    else:
        combat = (
            (("Def", "Def"), ("Resist", "Resist"), ("Absorb", "Exp")),
            (("Atk speed +", "AttackTime"), ("Move", "Speed"), ("Slots", "Slot")),
        )
        if kind == "chest":
            combat += ((("Elem.", "Attribute"), ("Cloak fit", "ReqVG")),)
        if family in (22, 23, 40):
            combat = ((("Base atk +", "Attack"), ("Max atk +", "AttackAdd")), *combat)
    return (
        ("Characteristics", combat),
        ("Bonuses", (*STATS, POOLS)),
        ("", (CRAFT, (("Enchant ID", "ReqLuc"),), PRICES)),
    )


def editable_fields(family: int) -> set[str] | None:
    sections = rows_for(family)
    if not sections:
        return None
    fields = {"Name", "Description", "Icon", "ReqDex", *(name for _, name in CLASSES), *(name for _, name in REQUIREMENTS)}
    fields.update(name for _, rows in sections for row in rows for _, name in row)
    if profile(family) == "mount":
        fields.add("Range")
    elif profile(family) != "accessory":
        fields.add("Model")
    return fields


def choices_for(family: int, name: str) -> tuple[tuple[int, str], ...]:
    kind = profile(family)
    if not kind:
        return ()
    if name == "ReqOG":
        return ((0, "Normal"), (1, "Restricted"))
    if name == "Slot":
        return tuple((i, str(i)) for i in range(7))
    if name == "ReqVG" and kind == "chest":
        return ((0, "Default (1)"), *((i, str(i)) for i in range(1, 9)))
    if name == "AttackTime" and kind == "weapon":
        return tuple(enumerate(ATTACK_SPEED_NAMES))
    if name == "AttackTime" and kind == "mount":
        return ((1, "Driver only"), (2, "Two riders"))
    if name == "Speed" and kind == "mount":
        return ((4, "4 · 7 units/s"), (5, "5 · 8 units/s"))
    return ()
