from contextlib import closing
from dataclasses import replace
from pathlib import Path
from sqlite3 import connect
from struct import pack
from tempfile import TemporaryDirectory
import unittest

from gx_items.asset_view import read_document
from gx_items.client_model_data import AltCatalog, CloakCatalog, EmblemCatalog, TextureCatalog
from gx_items.monster_data import CLIENT_BY_KEY, DB_FIELDS, DROPS, TABLE, MonsterCatalog, U32, read_mobs, read_monster_db, save_mobs
from gx_items.workspace import quote


class MonsterAndClientModelsTest(unittest.TestCase):
    def test_client_monster_edit_preserves_legacy_name_and_native_layout(self):
        name = b"ca\xf1a\0"
        row = pack("<I", len(name)) + name + bytes(31)
        raw = pack("<I", 2) + row + row
        catalog = MonsterCatalog(raw)
        edited = MonsterCatalog(catalog.encode({1: {"Day": 6, "HP": U32}}))
        self.assertEqual(edited.records[0], catalog.records[0])
        self.assertEqual(edited.records[1][0], name)
        self.assertEqual((edited.row(1)["Day"], edited.row(1)["HP"]), (6, U32))
        for value in (U32 + 1, -1):
            with self.assertRaises(ValueError):
                catalog.encode({1: {"HP": value}})
        with self.assertRaises(ValueError):
            CLIENT_BY_KEY["Day"].parse(7)

    def test_sqlite_changes_are_keyed_and_dated_backup_contains_previous_values(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mobs.sqlite"
            names = ", ".join(quote(field.key) + (" TEXT" if field.is_text else " INTEGER") for field in DB_FIELDS)
            with closing(connect(path)) as db, db:
                db.execute(f'CREATE TABLE "{TABLE}" (MobID INTEGER PRIMARY KEY, {names})')
                values = tuple("Creature" if field.is_text else 1 for field in DB_FIELDS)
                placeholders = ",".join("?" for _ in range(len(values) + 1))
                db.executemany(f'INSERT INTO "{TABLE}" VALUES ({placeholders})', [(4, *values), (5, *values)])
                db.execute(f'CREATE TABLE "{DROPS}" (MobID INTEGER, ItemOrder INTEGER, Grade INTEGER, DropRate INTEGER)')
                db.execute(f'INSERT INTO "{DROPS}" VALUES (4, 1, 11, 10)')
            before, drop_before = read_monster_db(path)
            with closing(connect(path)) as db, db:
                db.execute(f'UPDATE "{TABLE}" SET SP = 44 WHERE MobID=4')
            (saved, saved_drops), backup = save_mobs(
                path, before, {4: {"HP": U32}}, Path(tmp) / "backups",
                drop_baseline=drop_before, drop_edits={4: {1: (22, 45), 7: (100, 1_000_000)}},
            )
            self.assertEqual(saved[4]["HP"], U32)
            self.assertEqual(saved[4]["SP"], 44)
            self.assertEqual(saved[5], before[5])
            self.assertEqual(read_mobs(backup)[4]["HP"], before[4]["HP"])
            self.assertEqual(saved_drops[4], {1: [(22, 45)], 7: [(100, 1_000_000)]})
            self.assertEqual(read_monster_db(backup)[1], drop_before)
            with self.assertRaises(ValueError):
                save_mobs(path, saved, {}, Path(tmp) / "backups", drop_baseline=saved_drops,
                          drop_edits={4: {1: (2, 101)}})
            with self.assertRaises(ValueError):
                save_mobs(path, before, {4: {"HP": 123}}, Path(tmp) / "backups")
            with self.assertRaises(ValueError):
                save_mobs(path, saved, {4: {"Day": 6}}, Path(tmp) / "backups")
            self.assertEqual(read_mobs(path), saved)

    def test_alt_preserves_empty_slot_and_exact_parameter_bytes(self):
        raw = b"AL3" + pack("<2I", 2, 0) + pack("<I", 4) + b".ani" + pack("<II4f", 258, 4, 1, 2, 3, 4)
        catalog = AltCatalog(raw)
        row = replace(catalog.records[1], name="otro.ANI".encode())
        checked = AltCatalog(catalog.encode((catalog.records[0], row)))
        self.assertEqual(checked.records[0].name, b"")
        self.assertEqual(checked.records[1].parameters, catalog.records[1].parameters)

    def test_cloak_fixed_slots_preserve_padding_and_reference_ids(self):
        name = pack("<I", 0) + b"cloth.3DC".ljust(128, b"\0")
        initial_pc = pack("<I", 1) + name + pack("<I", 1) + bytes(524) + pack("<I", 1) + name
        catalog = CloakCatalog(initial_pc)
        table, new_index = CloakCatalog.reference(catalog.bodies, 0, "capa.3DC")
        self.assertEqual(new_index, 1)
        self.assertEqual(table[0], name)
        self.assertEqual(CloakCatalog(catalog.encode(catalog.records, table)).bodies, table)
        texture = b"old.dds\0".ljust(256, b"x")
        ctl = TextureCatalog(pack("<I", 1) + texture)
        self.assertEqual(TextureCatalog.name("old.dds", texture), texture)
        self.assertEqual(len(TextureCatalog.name("nueva.dds", texture)), 256)
        self.assertEqual(ctl.encode(ctl.records), ctl.raw)

    def test_emblem_tables_preserve_native_slots_and_texture_directory(self):
        original = b"001_front.dds\0".ljust(260, b"\xcc")
        data = pack("<I", 2) + original + original
        catalog = EmblemCatalog(data)
        changed = catalog.name("ñ.dds", catalog.records[1])
        revised = catalog.encode((original, changed))
        self.assertEqual(EmblemCatalog(revised).records[0], original)
        self.assertEqual(changed[:7], "ñ.dds".encode("utf-8") + b"\0")
        self.assertEqual(changed[-1], 0xcc)
        with self.assertRaises(ValueError):
            catalog.encode((original,) * 256)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / "EmblemFront-copy.dat"
            file.write_bytes(revised)
            folder = root / "BackgroundTex"
            folder.mkdir()
            texture = folder / "001_FRONT.DDS"
            texture.touch()
            mode, entries = read_document(file, None)
            self.assertEqual((mode, entries[0][0]), ("textures", "1 · 001_front.dds"))
            self.assertTrue(entries[0][1].samefile(texture))
