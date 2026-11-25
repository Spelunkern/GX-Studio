# ReqIg is the native reuse group for consumables. GX does not apply a delay
# to group 0 or unassigned groups.
CONSUMABLES = frozenset((25, 27, 28, 29, 38, 41, 43, 44, 78, 79, 80, 94, 99, 100, 101, 102, 103))
REUSE_SECONDS = {0: 0, 1: 15, 2: 20, 3: 25, 4: 30, 5: 60, 6: 120, 10: 600, 11: 2}


def spell_group_label(group: int) -> str:
    return f"{group} · {REUSE_SECONDS.get(group, 0)} s"
