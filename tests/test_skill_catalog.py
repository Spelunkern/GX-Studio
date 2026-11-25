import unittest
from contextlib import closing
from pathlib import Path
from sqlite3 import connect
from struct import pack, pack_into
from tempfile import TemporaryDirectory

from gx_items import seed
from gx_items.install import NPC_SKILLS, SKILLS, install_catalog
from gx_items.skill_catalog import NPCSkillCatalog, SKILL_FIELDS_BY_NAME, SkillCatalog
from gx_items.skill_layout import COMBOS, STATS, rank_columns
from gx_items.workspace import NPC_IDENTITY_TRIGGER, identity_trigger, quote, read_database


def skill_record(rank: int, name: bytes, amount: int) -> bytes:
    description = b"Original\0tail"
    body = bytearray(116)
    body[0] = rank
    body[21] = 4
    pack_into("<h", body, 88, amount)
    return pack("<I", len(name)) + name + pack("<I", len(description)) + description + body


def skill_file() -> bytes:
    return (pack("<I", 1) + skill_record(1, b"Caf\xe9\0legacy", 7) + skill_record(2, b"Rank two", 8)
            + skill_record(0, b"Reserved payload", -10) + bytes(6 * (8 + 116)))


class SkillCatalogTest(unittest.TestCase):
    def test_npc_install_uses_rank_and_keeps_the_player_namespace_intact(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, path = root / "NpcSkill.SData", root / "target.sqlite"
            catalog = NPCSkillCatalog(skill_file())
            edited = NPCSkillCatalog(catalog.encode({(1, 2): {"SuccessValue": 80}}))
            source.write_bytes(catalog.raw)
            with closing(connect(path)) as database, database:
                columns = ", ".join(map(quote, NPC_SKILLS.columns))
                database.execute(f"CREATE TABLE {NPC_SKILLS.name}(RowID INTEGER DEFAULT 0, SkillID, Rank, {columns})")
                database.execute(NPC_IDENTITY_TRIGGER)
                values = (1, 2, *catalog.sql_rows()[1, 2])
                marks = ", ".join("?" for _ in values)
                database.execute(f"INSERT INTO {NPC_SKILLS.name}(SkillID, Rank, {columns}) VALUES ({marks})", values)
                database.execute(f"CREATE TABLE {SKILLS.name}(SkillID, SkillLevel, SuccessValue)")
                database.execute(f"INSERT INTO {SKILLS.name} VALUES(1,2,36),(1,100,52)")

            install_catalog(source, catalog.raw, edited.raw, path, root / "backups", NPC_SKILLS,
                            edited.sql_rows(), catalog.sql_rows())
            install_catalog(source, edited.raw, edited.raw, path, root / "backups", NPC_SKILLS, edited.sql_rows())
            with closing(read_database(path)) as database:
                self.assertEqual(NPC_SKILLS.snapshot(database), edited.sql_rows())
                self.assertEqual(database.execute(f"SELECT SuccessValue FROM {SKILLS.name} ORDER BY SkillLevel").fetchall(),
                                 [(36,), (52,)])
                self.assertEqual(database.execute(f"SELECT RowID FROM {NPC_SKILLS.name} WHERE Rank=1").fetchone(), (2,))

    def test_npc_catalog_rejects_the_wire_namespace_marker_as_a_rank(self):
        raw = pack("<I", 1) + skill_record(100, b"Wire rank", 0) + bytes(8 * (8 + 116))
        with self.assertRaisesRegex(ValueError, "invalid rank 100"):
            NPCSkillCatalog(raw)

    def test_profile_columns_use_gx_damage_reflection_and_monster_fields(self):
        def fields(family, effect, *, noss=False):
            return {name for entry in rank_columns(family, effect, 0, noss=noss)
                    for name in ((entry,) if isinstance(entry, str) else entry[1])}

        self.assertTrue({"DamageHP", "KeepTime", "PrevSkillID"} <= fields(102, 5))
        self.assertTrue({"AbilityValue1", "AbilityValue2", "AbilityValue3"} <= fields(83, 4))
        self.assertNotIn("DamageHP", fields(83, 4))
        self.assertTrue({"ChangeType", "ChangeLevel"} <= fields(90, 3))
        self.assertTrue({"ChangeType", "ChangeLevel"} <= fields(90, 4))
        self.assertNotIn("PrevSkillID", fields(82, 4, noss=True))
        self.assertEqual(COMBOS["TargetType"][1], "Field Trap")
        self.assertEqual(STATS[23], "Physical Attack Power")
        self.assertTrue(set(range(52, 58)).isdisjoint(STATS))

    def test_native_skill_edits_preserve_other_ranks_and_reserved_slots(self):
        raw = seed.encode_like(seed.SIGNATURE.ljust(64, b"\0"), skill_file())
        catalog = SkillCatalog(raw)
        self.assertEqual(tuple(catalog.records), ((1, 1), (1, 2)))
        edited = SkillCatalog(catalog.encode({(1, 2): {"Name": "Rango número dos", "AbilityValue1": -32768}}))
        self.assertTrue(edited.raw.startswith(seed.SIGNATURE))
        first = catalog.records[1, 1]
        self.assertEqual(edited.plain[first.start:first.end], catalog.plain[first.start:first.end])
        self.assertEqual(edited.plain[edited.records[1, 2].end:], catalog.plain[catalog.records[1, 2].end:])
        self.assertEqual(edited.value((1, 2), SKILL_FIELDS_BY_NAME["Name"]), "Rango número dos")
        self.assertEqual(edited.value((1, 2), SKILL_FIELDS_BY_NAME["AbilityValue1"]), -32768)

    def test_skill_install_uses_both_identity_columns(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, path = root / "Skill.SData", root / "target.sqlite"
            catalog = SkillCatalog(skill_file())
            source.write_bytes(catalog.raw)
            after = SkillCatalog(catalog.encode({(1, 2): {"AbilityValue1": -25}}))
            with closing(connect(path)) as database, database:
                columns = ", ".join(map(quote, SKILLS.columns))
                database.execute(f'CREATE TABLE {SKILLS.name}(RowID INTEGER DEFAULT 0, SkillID, SkillLevel, {columns})')
                database.execute(identity_trigger(SKILLS.name))
                for (skill_id, rank), row in catalog.sql_rows().items():
                    values = (skill_id, rank, *row)
                    marks = ", ".join("?" for _ in values)
                    database.execute(f"INSERT INTO {SKILLS.name}(SkillID, SkillLevel, {columns}) VALUES ({marks})", values)
                database.execute(f"UPDATE {SKILLS.name} SET ReqLevel=57 WHERE SkillID=1 AND SkillLevel=2")
                database.execute(f"INSERT INTO {SKILLS.name}(SkillID, SkillLevel) VALUES(1,3)")

            backup = install_catalog(source, catalog.raw, after.raw, path, root / "backups", SKILLS,
                                     after.sql_rows(), catalog.sql_rows())
            query = f"SELECT SkillLevel, AbilityValue1, ReqLevel FROM {SKILLS.name} WHERE SkillID=1 ORDER BY SkillLevel"
            with closing(read_database(path)) as database, closing(read_database(backup / "rapture.sqlite")) as copy:
                self.assertEqual(database.execute(query).fetchall(), [(1, 7, 0), (2, -25, 57), (3, None, None)])
                self.assertEqual(copy.execute(query).fetchall(), [(1, 7, 0), (2, 8, 57), (3, None, None)])

            install_catalog(source, after.raw, after.raw, path, root / "backups", SKILLS, after.sql_rows())
            with closing(read_database(path)) as database:
                self.assertEqual(database.execute(query).fetchall(), [(1, 7, 0), (2, -25, 0)])
