import unittest
from contextlib import closing
from pathlib import Path
from sqlite3 import connect
from struct import pack, pack_into
from tempfile import TemporaryDirectory

from gx_items.equipment_models import ModelAssets
from gx_items.quest_catalog import QuestReferences, quest_references


def string(text: str = "") -> bytes:
    encoded = text.encode("utf-8")
    return pack("<I", len(encoded)) + encoded


def quest_catalog() -> bytes:
    native_links = bytearray(256 * 256 * 8)
    offset = (28 * 256 + 9) * 8
    native_links[offset:offset + 4] = pack("<IHH", 2, 1, 1)
    hunt = bytearray(165)
    hunt[65] = 2  # Kill objective
    pack_into("<HB", hunt, 79, 2, 1)
    collect = bytearray(165)
    collect[65] = 1  # Collection objective
    collect[69:72] = bytes((28, 9, 3))
    return b"".join((
        bytes(13 * 4), native_links, pack("<I", 2),
        pack("<H", 99), string("Hunt in the woods"), string(), hunt, string() * 7,
        pack("<H", 42), string("Collect the sap"), string(), collect, string() * 7,
    ))


class QuestCatalogTest(unittest.TestCase):
    def test_references_use_gx_row_ids_and_read_only_mob_sources(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Npc").mkdir()
            (root / "Monster").mkdir()
            (root / "Npc/NpcQuest.SData").write_bytes(quest_catalog())
            (root / "Monster/Monster.SData").write_bytes(pack("<I", 3) + b"".join(
                string(name) + bytes(29) + pack("<H", index)
                for name, index in (("", 0), ("Tree spirit", 1009), ("Wólf", 0))
            ))
            assets = ModelAssets(root)
            quest_names = ((1, "Hunt in the woods"), (2, "Collect the sap"))
            self.assertEqual(quest_references(28009, assets), QuestReferences(
                quest_names, ((1, "Tree spirit", "Item drop"), (2, "Wólf", "Quest objective")),
            ))
            self.assertEqual(quest_references(28008, assets), QuestReferences((), ()))

            database_path = root / "rapture.sqlite"
            with closing(connect(database_path)) as database, database:
                database.execute("CREATE TABLE PS_GameDefs_Mobs (MobID, MobName, QuestItemID)")
                database.execute("INSERT INTO PS_GameDefs_Mobs VALUES (2, 'Forest wolf', 1009)")
            self.assertEqual(quest_references(28009, assets, database_path), QuestReferences(
                quest_names, ((2, "Forest wolf", "Drop + objective"),),
            ))
