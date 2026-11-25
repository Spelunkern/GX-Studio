from dataclasses import dataclass

from .equipment_models import ARMOR_TYPES, WEAPON_TYPES


PROFESSIONS = dict(zip((*range(12), 15, 16, 100, 101, 117, 200), (
    "Generic Merchant", "Weapon Merchant", "Protection Merchant", "Accessory Merchant", "Liquid Medecine Merchant",
    "Professional Trader", "Riding Merchant", "Mantle Merchant", "Badge Merchant", "Bank Teller",
    "Gold Bar Merchant", "Episo 6.x Merchant", "Professional Blacksmith 1", "Professional Blacksmith 2",
    "Etin Manager", "Etin Seller", "Action Board", "PvP Quests",
)))
CLASSES = ("Regular", "Noble", "Worship", "Heroic", "Dread", "Legendary", "Goddess")
GENERIC_TYPES = (
    *((key, f"Good Items {key} (type {key})") for key in (25, 44, 78, 79, 80, 100, 101, 102, 103)),
    *((key, f"Quest Items {index} (type {key})") for index, key in enumerate((27, 28, 29, 99), 1)),
)
COMMON_WEAPONS = ((7, "1-Hand Blunts"), (8, "2-Hands Blunts"))
WEAPON_TYPES_BY_FACTION = {
    1: ((1, "1-Hand Swords"), (2, "2-Hands Swords"), *COMMON_WEAPONS, (5, "Dual Swords"),
        (6, "Spears"), (9, "Reverse Swords"), (10, "Daggers"), (13, "Bows"), (14, "Cross-Bows"),
        (12, "Staffs"), (15, "Knuckles")),
    2: ((3, "1-Hand Axes"), (4, "2-Hands Axes"), *COMMON_WEAPONS, (5, "Dual Axes"),
        (6, "Spears"), (9, "Reverse Swords"), (10, "Daggers"), (13, "Bows"), (11, "Javelins"),
        (12, "Staffs"), (15, "Knuckles")),
}
FACTION_WEAPON_VARIANTS = {1: {49, 51, 53, 55, 60, 62}, 2: {50, 52, 54, 56, 61, 63}}
GEAR_TYPES_BY_FACTION = {
    1: ((0, "All Gears"), (16, "Helmets"), (17, "Armors"), (18, "Pants"),
        (21, "Boots"), (20, "Gantlets"), (19, "Shields")),
    2: ((0, "All Gears"), (31, "Caps"), (32, "Protectors"), (33, "Legging"),
        (36, "Walkers"), (35, "Mufflers"), (34, "Shields")),
}
ACCESSORIES = {0: {22, 23, 37, 40, 96, 97}, 23: {23, 96}, 40: {40, 97}, 22: {22, 37}, 30: {30, 95}}
LIGHT_ARMOR = {16, 17, 18, 20, 21, 66, 67, 68, 70, 71, 72, 73, 74, 76, 77}
SHIELDS = {1: {19, 69, 75}, 2: {34, 84, 90}}
FACTION_REQUIRED = ((None, "Cannot sell to both factions"),)
EMPTY_PRODUCT = (0, 0)


@dataclass(frozen=True, slots=True)
class MerchantItem:
    name: str
    level: int
    description: str
    slots: int
    country: int
    effect: int
    special: int
    recovers_stats: bool
    icon: int = 0


def item_types(profession, faction):
    if profession == 0:
        return GENERIC_TYPES
    if profession == 1:
        return WEAPON_TYPES_BY_FACTION.get(faction, FACTION_REQUIRED)
    if profession == 2:
        return GEAR_TYPES_BY_FACTION.get(faction, FACTION_REQUIRED)
    if profession == 3:
        return ((0, "Jewelry"), (23, "Amulets"), (40, "Loops"), (22, "Rings"), (30, "Lapis"))
    if profession == 6:
        return ((42, "All Mounts"),) if faction in (1, 2) else FACTION_REQUIRED
    if profession == 7:
        return {1: ((24, "Capes"),), 2: ((39, "Mantles"),)}.get(faction, FACTION_REQUIRED)
    return ((0, "All"),)


def item_matches(profession, faction, category, slots, family, item):
    if category is None or not item.name or item.name.startswith("?"):
        return False
    if profession == 0:
        return family == category
    if profession == 1:
        country_allowed = item.country == 6 or (faction == 1 and item.level and item.country == 2) or (
            faction == 2 and item.country == 5
        )
        return (WEAPON_TYPES.get(family) == category and family not in FACTION_WEAPON_VARIANTS.get(3 - faction, ())
                and country_allowed and (slots is None or slots == item.slots))
    if profession == 2:
        if not item.level or (slots is not None and slots != item.slots):
            return False
        if family in SHIELDS.get(faction, ()):
            return True
        part = ARMOR_TYPES.get(family)
        return part is not None and (family in LIGHT_ARMOR) == (faction == 1) and (
            category == 0 or ARMOR_TYPES.get(category) == part
        )
    if profession == 3:
        return family in ACCESSORIES.get(category, ())
    if profession == 4:
        if family == 25:
            return item.recovers_stats or item.effect != 0 or item.special not in (0, 42)
        return (family == 44 and bool(item.effect)) or (family == 100 and 280 <= item.effect <= 300)
    if profession == 6:
        return family == 42 and item.country in ((0, 1) if faction == 1 else (3, 4))
    if profession == 7:
        return family == category and item.level > 0
    return family in {10: {94}, 11: {78, 79, 80}, 101: {43}}.get(profession, ())
