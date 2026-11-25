from contextlib import closing
from pathlib import Path
from sqlite3 import connect
from struct import pack, pack_into
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gx_items import seed
from gx_items.catalog import Catalog
from gx_items.consumable_fields import spell_group_label
from gx_items.fields import FIELDS_BY_NAME
from gx_items.gx_connection import connect_gx, detect_server_data, read_catalog, update_item
from gx_items.lapis_fields import choices_for_lapis
from gx_items.npc_skills import NPCSkill, read_npc_skills, skill_icon_location
from gx_items.workspace import COLUMNS, Workspace, quote


def string(raw: bytes) -> bytes:
    return pack("<I", len(raw)) + raw


def fixture() -> bytes:
    body = bytearray(81)
    body[:4] = bytes((1, 1, 4, 2))
    return pack("<II", 1, 1) + string(b"Test") + string(b"") + body


def setup_database(path: Path):
    with closing(connect(path)) as database, database:
        columns = ", ".join(map(quote, COLUMNS))
        database.execute(f'CREATE TABLE PS_GameDefs_Items (ItemID, Type, TypeID, {columns})')
        database.execute(
            "INSERT INTO PS_GameDefs_Items (ItemID, Type, TypeID, ItemName, Count) "
            "VALUES (1001, 1, 1, 'Test', 1)"
        )


class ItemCatalogTest(unittest.TestCase):
    def test_consumable_spell_groups_use_seconds_and_preserve_legacy_codes(self):
        self.assertEqual(spell_group_label(11), "11 · 2 s")
        self.assertEqual(spell_group_label(10), "10 · 600 s")
        self.assertEqual(spell_group_label(7), "7 · 0 s")

    def test_npc_skill_preview_uses_rank_one_and_keeps_atlas_coordinates(self):
        record = bytearray(116)
        record[0], record[21], record[54], record[55] = 1, 3, 1, 8
        pack_into("<H", record, 1, 1017)
        skill = string(b"  Faint  ") + string(b"Stuns targets.") + record
        padding = (string(b"") + string(b"") + bytes(116)) * 8
        raw = pack("<I", 1) + skill + padding
        self.assertEqual(read_npc_skills(raw), {1: NPCSkill("Faint", "Stuns targets.", 1017, 3, 1, 8)})
        self.assertEqual(skill_icon_location(1017), ("icon_skill2_dds.png", 1, 0))
        self.assertIsNone(skill_icon_location(0))
        with self.assertRaises(ValueError):
            read_npc_skills(raw[:-1])

    def test_lapis_cost_tiers_use_gx_base_percent_and_native_price_codes(self):
        lapis = dict(choices_for_lapis(30, "ReqIg"))
        self.assertEqual(tuple(lapis), (*range(30, 41), *range(96, 101)))
        self.assertEqual(lapis[30], "49% · 1,000 gold")
        self.assertEqual(lapis[33], "25% · 22,965 gold")
        self.assertEqual(lapis[96], "1% · 2,500,000 gold")
        lapisia = dict(choices_for_lapis(95, "ReqIg"))
        self.assertEqual(lapisia[96], "2,500,000 gold")

    def test_compatible_seed_envelope(self):
        original = fixture()
        encrypted = seed.encode_like(seed.SIGNATURE.ljust(64, b"\0"), original)
        # Cross-checked against GX's Go SEED implementation.
        self.assertEqual(encrypted[64:80].hex(), "2ea483e137e5f20d66af8c069b005998")
        catalog = Catalog(encrypted)
        self.assertEqual(catalog.encode({}), encrypted)
        edited = Catalog(catalog.encode({1001: {"Name": "Lâmina GX"}}))
        self.assertTrue(edited.encrypted)
        self.assertEqual(edited.value(edited.items[1001], FIELDS_BY_NAME["Name"]), "Lâmina GX")
        self.assertEqual(edited.encode({1001: {"Name": "Test"}}), encrypted)

    def test_patch_preserves_untouched_record_bytes(self):
        raw = fixture()
        first = raw[8:]
        reserved = string(b"") + string(b"") + bytes(81)
        original = pack("<II", 1, 2) + first + reserved
        output = Catalog(original).encode({1001: {"Icon": 23}})
        icon = 8 + 4 + 4 + 4 + 3
        self.assertEqual(output, original[:icon] + bytes((23,)) + original[icon + 1:])

    def test_utf8_edit_preserves_untouched_legacy_text(self):
        legacy_name = b"Espada \xf1\0"
        original = pack("<II", 1, 1) + string(legacy_name) + string(b"") + fixture()[-81:]
        catalog = Catalog(original)
        self.assertEqual(catalog.value(catalog.items[1001], FIELDS_BY_NAME["Name"]), "Espada ñ")
        self.assertEqual(catalog.encode({}), original)
        description = "Descripción · 日本語 · ⚔️"
        output = catalog.encode({1001: {"Description": description}})
        reopened = Catalog(output)
        self.assertIn(string(legacy_name), output)
        self.assertIn(string(description.encode("utf-8")), output)
        self.assertEqual(reopened.value(reopened.items[1001], FIELDS_BY_NAME["Description"]), description)
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            catalog.encode({1001: {"Name": "Invalid \ud800"}})

    def test_export_copies_committed_wal_and_updates_only_mapped_changes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            item_file = root / "Item.SData"
            item_file.write_bytes(fixture())
            sqlite = root / "source.sqlite"
            setup_database(sqlite)
            with closing(connect(sqlite)) as writer:
                writer.execute("PRAGMA journal_mode=WAL")
                with writer:
                    writer.execute("CREATE TABLE example (value TEXT)")
                    writer.execute("INSERT INTO example VALUES ('committed in WAL')")
                source = Workspace(item_file, sqlite)
                result = source.export(root, {1001: {"Name": "Espada", "Count": 3, "Icon": 7}})
                output = Workspace(result / "data/Item/Item.SData", result / "rapture.sqlite")
                self.assertEqual(output.sql_rows[1001]["ItemName"], "Espada")
                self.assertEqual(output.sql_rows[1001]["Count"], 255)
                with closing(connect(result / "rapture.sqlite")) as copy:
                    self.assertEqual(copy.execute("SELECT value FROM example").fetchone(), ("committed in WAL",))
                self.assertEqual(writer.execute("SELECT ItemName FROM PS_GameDefs_Items").fetchone(), ("Test",))
                self.assertEqual(item_file.read_bytes(), fixture())

    def test_stale_sqlite_leaves_no_partial_export(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            item_file = root / "Item.SData"
            item_file.write_bytes(fixture())
            sqlite = root / "source.sqlite"
            setup_database(sqlite)
            source = Workspace(item_file, sqlite)
            with closing(connect(sqlite)) as writer, writer:
                writer.execute("UPDATE PS_GameDefs_Items SET ItemName = 'Changed elsewhere'")
            with self.assertRaisesRegex(ValueError, "SQLite changed"):
                source.export(root, {1001: {"Name": "Draft"}})
            self.assertEqual(set(root.iterdir()), {item_file, sqlite})

    def test_detected_database_and_ambiguous_installations(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.sqlite"
            setup_database(path)
            with patch("gx_items.gx_connection.running_databases",
                       return_value={path: {"gx_world", "gx_login"}}):
                status = connect_gx()
                self.assertTrue(status.editable)
                self.assertEqual(status.path, path.resolve())
            with patch("gx_items.gx_connection.running_databases",
                       return_value={path: {"gx_world"}, path.with_suffix(".db"): {"gx_login"}}):
                self.assertEqual(connect_gx().status, "Select database")

    def test_selected_server_uses_its_configured_runtime_and_never_its_seed(self):
        with TemporaryDirectory() as directory:
            server = Path(directory).resolve() / "GX Server"
            game = server / "datos ñ" / "game"
            game.mkdir(parents=True)
            (server / ".local").mkdir()
            (server / ".local" / "runtime.env").write_text(
                "RAPTURE_DATA_DIR=datos ñ\nRAPTURE_DB_PATH=runtime/active.sqlite\n", encoding="utf-8",
            )
            database = server / "runtime" / "active.sqlite"
            database.parent.mkdir()
            seed_database = game.parent / "rapture.sqlite"
            setup_database(seed_database)
            with patch("gx_items.gx_connection.running_databases", return_value={}) as detected, \
                    patch("gx_items.gx_connection.gx_processes", return_value=()):
                self.assertEqual(connect_gx(server_folder=server).status, "Disconnected")
                detected.assert_called_with(game.parent)
                setup_database(database)
                self.assertEqual(connect_gx(server_folder=server).path, database)
                self.assertEqual(detect_server_data(database), game.parent)
                self.assertEqual(connect_gx(seed_database, server).path, seed_database)

    def test_direct_sql_update_rejects_stale_edits(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.sqlite"
            setup_database(path)
            before = read_catalog(path)[1001]
            name = "Espada ñ 日本語 ⚔️"
            backups = path.parent / "backups"
            backup = update_item(path, 1001, before, {"ItemName": name, "Range": 300}, backups=backups)
            self.assertEqual(read_catalog(backup / "rapture.sqlite")[1001], before)
            with self.assertRaisesRegex(ValueError, "changed in SQLite"):
                update_item(path, 1001, before, {"ItemName": "Stale", "Count": 42}, backups=backups)
            self.assertEqual(list(backups.iterdir()), [backup])
            after = read_catalog(path)[1001]
            self.assertEqual((after["ItemName"], after["Range"], after["Count"]), (name, 300, 1))
