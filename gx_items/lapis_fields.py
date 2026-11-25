LAPISIA = 95
LAPIS_DEBUFF_FAMILIES = frozenset((30, 98))
LAPIS_FAMILIES = LAPIS_DEBUFF_FAMILIES | {LAPISIA}

# GX World's lapisiaFits uses these native item fields as target flags.
TARGET_ROWS = (
    (("Helmet", "Country"), ("Gauntlets", "Shootrogue"), ("Shoes", "Attackmage"),
     ("Pants", "Defensefighter"), ("Armor", "Attackfighter")),
    (("Shield", "Patrolrogue"), ("Weapon", "MinLevel"), ("Necklace", "Grow"),
     ("Loop", "ReqStr"), ("Ring", "Defensemage")),
)
STAT_ROWS = (
    (("STR", "Str"), ("LUC", "Luc"), ("HP", "RecoveryHP"), ("Def", "Def")),
    (("DEX", "Dex"), ("INT", "Int"), ("SP", "RecoverySP"), ("Resist", "Resist")),
    (("REC", "Rec"), ("WIS", "Wis"), ("MP", "RecoveryMP"), ("Absorb", "Exp")),
)
# GX's GetGemMoneyByGrade table, keyed by the native ReqIg byte.
GOLD_BY_COST = {
    **dict(enumerate((1000, 4095, 11250, 22965, 41280, 137900, 365000, 480000, 627000, 814000, 1040000), 30)),
    **dict(enumerate((2500000, 3500000, 5500000, 7500000, 9800000), 96)),
}
NPC_EFFECT_NAMES = {
    0: "None", 1: "Sleep", 2: "Stun", 3: "Silence", 4: "Darkness", 5: "Pause", 6: "Slow",
    7: "Dying", 8: "Death", 9: "Poison", 10: "Illness", 11: "Delusion", 12: "Doom",
    13: "Fear", 14: "Dull", 15: "Bad Luck", 16: "Recovery",
}
for first_id, effect in (
    (205, "Faint"), (208, "Freezing"), (211, "Raid Block"), (214, "Spell Block"),
    (217, "Crawl"), (220, "Panic"), (223, "Curse"), (226, "Low-Speed"),
    (229, "Toxin"), (232, "Disease"), (235, "Confusion"), (238, "Kwip's Kurse"), (241, "Slumber"),
):
    NPC_EFFECT_NAMES[first_id] = f"{effect} Lv 1"
    NPC_EFFECT_NAMES[first_id + 1] = f"{effect} Lv 2"


def base_link_chance(code: int) -> int:
    return min(100, max(1, 289 - 8 * code))


def cost_label(family: int, code: int) -> str:
    gold = f"{GOLD_BY_COST.get(code, 0):,} gold"
    return gold if family == LAPISIA else f"{base_link_chance(code)}% · {gold}"


def choices_for_lapis(family: int, name: str) -> tuple[tuple[int, str], ...]:
    if name == "ReqIg":
        return tuple((code, cost_label(family, code)) for code in GOLD_BY_COST)
    if name == "Slot":
        return ((0, "0 slots"), (1, "1 slot"), (2, "2 slots"), (3, "3 slots"))
    if name == "ReqOG":
        return ((0, "Normal"), (1, "Restricted"))
    return ()
