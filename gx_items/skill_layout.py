import re

from .fields import CLASSES
from .item_view import ELEMENTS, FACTIONS
from .skill_catalog import LEGACY, SKILL_FIELDS, SKILL_FIELDS_BY_NAME

TOON, NOSS = range(2)
WEAPONS = ("1H Sword", "2H Sword", "1H Axe", "2H Axe", "Dual Swords", "Spear", "1H Blunt", "2H Blunt",
           "Reverse Sword", "Dagger", "Javelin", "Staff", "Bow", "Crossbow", "Knuckles", "Shield")
CLASS_FIELDS = tuple(key for _, key in CLASSES)
WEAPON_FIELDS = (*(f"NeedWeapon{i}" for i in range(1, 16)), "Shield")
FAMILIES = {
    0: "None", 1: "Weapon Power Up", 2: "Backpack", 4: "Passive Defense", 5: "Interpretation",
    6: "Potential Defense", 8: "Weapon Mastery", 10: "Defense Power Up", 21: "Unique Hit Attack",
    22: "Multiple Hit Attack", 23: "Eraser", 25: "HP, MP, SP Reduction", 27: "Energy Drain", 41: "Healing",
    42: "Dispel", 43: "Sacrifice", 61: "Periodical Debuff", 62: "Subtracting Debuff", 63: "Flite",
    64: "Stun", 65: "Immobilize", 66: "Sleep", 67: "Prevent Attack", 68: "Remove Attribute", 69: "Taunt",
    72: "Provoke", 74: "Energy Blackhole", 75: "Mental Storm, Confusion", 76: "Soul Menace",
    77: "Mental Storm, Distortion", 81: "Periodical Heal", 82: "Buff, Noss", 83: "Damage Reflection",
    84: "Fire Thorn", 85: "Block Magic Attack", 86: "Block Shooting Attack", 87: "Health Assistant",
    88: "Stealth", 90: "Evolution / Degeneration", 91: "Ability Exchange", 92: "Untouchable", 93: "Detection",
    94: "Elemental Attack", 95: "Elemental Protection", 96: "Dance of Death", 97: "Mass Ambush",
    98: "Etain Shield", 101: "Trap", 102: "Fire Bush", 121: "Resurrection", 122: "Soul Burn",
    123: "Scouting", 125: "Town Portal", 126: "Mental Impact", 1062: "Cure of Goddess",
}
STATS = {
    0: "None", 1: "Health (base)", 2: "Mana (base)", 3: "Stamina (base)", 4: "Strength (STR)",
    5: "Reaction (REC)", 6: "Intelligence (INT)", 7: "Wisdom (WIS)", 8: "Dexterity (DEX)", 9: "Luck (LUC)",
    10: "Health (regain)", 11: "Stamina (regain)", 12: "Mana (regain)", 13: "SP Cost Reduction", 14: "MP Cost Reduction",
    15: "Attack Range", 16: "Attack Speed", 17: "Movement Speed", 18: "Critical Attack Rate", 19: "Spell Cooldown Time",
    20: "Physical Attack Rate", 21: "Shooting Attack Rate", 22: "Magic Attack Rate", 23: "Physical Attack Power",
    24: "Shooting Attack Power", 25: "Magic Attack Power", 26: "Physical Defense", 27: "Shooting Defense",
    28: "Magic Resistance", 29: "Physical Evasion Rate", 30: "Shooting Evasion Rate", 31: "Magic Evasion Rate",
    32: "Disable Physical Attack", 33: "Disable Shooting Attack", 34: "Disable Magic Attack",
    35: "Experience Gain Rate", 38: "Experience Loss Rate", 39: "Warehouse Recall", 44: "Gold Drop Rate",
}
REFLECT_FIELDS = {f"AbilityValue{i + 1}": label for i, label in enumerate(("Physical", "Ranged", "Magic"))}
COMBOS = {field: dict(enumerate(options)) for field, options in {
    "Country": FACTIONS,
    "Grow": ("Easy or higher", "Normal or higher", "Difficult or higher", "Ultimate"),
    "TypeShow": ("Noss / Other", "Passive", "Basic", "Combat", "Special"),
    "TypeAttack": ("Passive", "Physical Attack", "Shooting Attack", "Magic Attack"),
    "TypeEffect": ("Buff, Noss, …", "Basic Damage", "Healing, Dispel", "Debuff", "Buff", "Fixed Damage", "Other"),
    "TargetType": ("Default", "Field Trap", "Caster", "Selected target", "Party", "Party / nearby allies",
                   "Enemies near caster", "Enemies near target / selected ally", "Raid (healing or buff)"),
    "AttrType": ELEMENTS,
    "StateType": ("None", "Sleep", "Stun", "Darkness", "Silence", "Immobilize", "Slow", "Flat Damage",
                  "Death's Touch", "HP damage over time", "SP damage over time", "MP damage over time",
                  "Mental Smasher", "Lower Attack or Defense", "Slump Shot", "Misfortunate"),
    "FixRange": ("Seconds", "Hours / persists on death", "Seconds / persists on death"),
    "DefenceType": ("None", "Melee Attacks", "Ranged Attacks", "Magical Attacks"),
    "DamageType": ("Fixed damage", "Attack power plus bonus", "Attack power multiplier", "Caster pool percentage",
                   "Target pool percentage reduction", "Target pool percentage damage"),
}.items()}
COMBOS.update({f"AbilityType{i}": STATS for i in range(1, 4)})
COMBOS["TypeDetail"] = FAMILIES
COMBOS["Weapon1"] = COMBOS["Weapon2"] = {0: "None", **dict(enumerate(WEAPONS[:-1], 1)), 19: "Shield"}
COMBOS["ChangeType"] = {0: "None", 10: "Evolution 1", 11: "Evolution 2", 12: "Evolution 3",
                        100: "Disguise", 101: "Degeneration", 102: "Monster Copy"}

LIST_FIELDS = (
    ("ID", "ID", 78), ("Name", "Name", 135), ("StateType", "State", 50), ("TypeDetail", "Type", 156),
    ("DamageType", "Dmg Type", 66), ("Description", "Desc", 250),
)

RANK_LABELS = {
    "ReqLevel": "Lvl", "SkillPoint": "Pts", "SP": "Cost SP", "MP": "Cost MP", "ReadyTime": "Cast",
    "ResetTime": "Reset", "Chance": "Chance", "AttrType": "Element", "TargetType": "Target", "AttackRange": "Dist.",
    "ApplyRange": "Range", "MultiAttack": "Hits", "KeepTime": "Keep Time", "Weaponvalue": "Incr. Speed",
    "LimitHP": "HP limit", "DefenceValue": "Defence", "Description": "Text", "Arrow": "Taunt",
    "PrevSkillID": "Prev Skill", "ChangeLevel": "Monster ID", "ChangeType": "Form", "DamageHP": "HP Damage",
    **{f"AbilityValue{i}": f"Stat. {i}" for i in range(1, 4)},
}


def rank_title(name: str) -> str:
    return re.sub(r"\s*\bLvl?\s*\d*$", "", name.strip().removesuffix("...").rstrip(), flags=re.IGNORECASE).strip()


def pools(label: str, prefix: str):
    return label, tuple(f"{prefix}{pool}" for pool in ("HP", "SP", "MP"))


ABILITIES = ("Abilities", tuple(f"AbilityValue{i}" for i in range(1, 4)))
BASE_DAMAGE = pools("Base Damage", "Damage")
EXTRA_DAMAGE = pools("Add. Damage", "AddDamage")
PERIODIC_DAMAGE = pools("Time Damage", "TimeDamage")
HEAL = pools("Heal", "Heal")
PERIODIC_HEAL = pools("Heal over time", "TimeHeal")
REFLECTION = ("Reflection", tuple(REFLECT_FIELDS))
WEAPON_PASSIVES = frozenset((1, 8, 10))
STAT_FAMILIES = frozenset((4, 6, 21, 22, 62, 64, 65, 67, 82, 91))
DETAIL_COLUMNS = {
    21: (ABILITIES, BASE_DAMAGE), 22: ("MultiAttack", ABILITIES, BASE_DAMAGE),
    25: (BASE_DAMAGE, EXTRA_DAMAGE), 27: (BASE_DAMAGE,), 41: (HEAL,), 61: (BASE_DAMAGE, PERIODIC_DAMAGE),
    62: (BASE_DAMAGE, ABILITIES), 63: (BASE_DAMAGE, PERIODIC_DAMAGE),
    64: (ABILITIES, "DamageHP"), 65: (ABILITIES, "DamageHP"), 66: ("DamageHP",), 67: (BASE_DAMAGE, ABILITIES),
    69: ("Arrow",), 72: ("Arrow",), 74: (BASE_DAMAGE,), 81: (PERIODIC_HEAL,), 82: (ABILITIES,),
    83: (REFLECTION,), 84: (BASE_DAMAGE, "Arrow"), 85: ("DefenceValue",), 86: ("DefenceValue",),
    90: ("ChangeType", "ChangeLevel"), 91: (ABILITIES,), 101: (BASE_DAMAGE,), 102: ("DamageHP",), 126: ("DamageHP",),
}


def rank_columns(family: int, effect: int, state: int, *, noss: bool):
    requirements = ("ReqLevel", "SkillPoint") if noss else ("ReqLevel", "SkillPoint", "PrevSkillID")
    casting = ("SP", "MP", "ReadyTime", "ResetTime")
    ranges = ("TargetType", "AttackRange", "ApplyRange")
    if family in WEAPON_PASSIVES:
        return (*requirements, "Weaponvalue")
    if family == 2:
        return (*requirements, "Bag")
    if family in (4, 5, 6):
        extras = {4: (ABILITIES,), 5: ("Description",), 6: (ABILITIES, "LimitHP")}
        return (*requirements, *extras[family])
    duration = family in (72, 101, 102) or state in (1, 2, 3, 4, 5, 6, 9, 10, 11) or (effect in (3, 4) and family != 69)
    extra = DETAIL_COLUMNS.get(family, ())
    if noss:
        return (*requirements, *casting, "Chance", *ranges, *extra, "KeepTime", "FixRange")
    return (*requirements, *casting, "Chance", "AttrType", *ranges, *extra, *(("KeepTime",) if duration else ()))


def column_label(name: str) -> str:
    return RANK_LABELS[name] if name in RANK_LABELS else SKILL_FIELDS_BY_NAME[name].label


def additional_rank_fields(columns):
    visible = {field for column in columns for field in ((column,) if isinstance(column, str) else column[1])}
    visible.update(("Name", "Description", "SuccessValue"))
    return tuple(field.name for field in SKILL_FIELDS if field.name not in visible and field.section != LEGACY)
