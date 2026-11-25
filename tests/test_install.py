import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from sqlite3 import connect
from struct import pack
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gx_items import seed
from gx_items.cash_catalog import HEADER, SLOT, CashCatalog, EMPTY_BUNDLE
from gx_items.install import ITEMS, PRODUCTS, install_catalog, product_rows, replace_file
from gx_items.workspace import quote, read_database


def cash_file() -> bytes:
    bundle = (*EMPTY_BUNDLE[:23], (1001, 255))
    texts = (b"Caf\xe9\0reserved", b"TEST", b"Description")
    return (pack("<I", 1) + HEADER.pack(0, 1, 14, 10) + b"".join(SLOT.pack(*slot) for slot in bundle)
            + b"".join(pack("<I", len(text)) + text for text in texts))


class InstallTest(unittest.TestCase):
    def test_cash_native_round_trip_preserves_legacy_text_and_last_slot(self):
        raw = seed.encode_like(seed.SIGNATURE.ljust(64, b"\0"), cash_file())
        catalog = CashCatalog(raw)
        base, = catalog.products
        edited = replace(base, description="Descripción", cost=0xffffffff)
        saved = CashCatalog(catalog.encode({0: edited}))
        self.assertEqual(catalog.encode({0: base}), raw)
        self.assertEqual(saved.strings[0][0], b"Caf\xe9\0reserved")
        self.assertEqual(saved.products, (edited,))
        self.assertEqual(saved.products[0].bundle[23], (1001, 255))

    def test_targeted_and_full_install_preserve_other_tables_and_backup_committed_wal(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, path = root / "Item.SData", root / "target.sqlite"
            source.write_bytes(b"original")
            with closing(connect(path)) as writer:
                writer.execute("PRAGMA journal_mode=WAL")
                columns = ", ".join(map(quote, ITEMS.columns))
                writer.execute(f"CREATE TABLE {ITEMS.name}(ItemID INTEGER PRIMARY KEY, {columns})")
                writer.execute("CREATE TABLE other(marker TEXT)")
                writer.execute("INSERT INTO other VALUES ('already committed to WAL')")
                writer.execute(f"INSERT INTO {ITEMS.name}(ItemID,Type,TypeID,ItemName,Reqlevel) VALUES (1001,1,1,'old',78)")
                writer.execute(f"INSERT INTO {ITEMS.name}(ItemID,Type,TypeID,ItemName) VALUES (1002,1,2,'server-only')")
                writer.commit()

                base = (1, 1, "old", 1, *((None,) * (len(ITEMS.columns) - 4)))
                changed = (1, 1, "new", *base[3:])
                backup = install_catalog(source, b"original", b"targeted", path, root / "backups", ITEMS,
                                         {1001: changed}, {1001: base})
                with closing(read_database(backup / "rapture.sqlite")) as copy:
                    self.assertEqual(copy.execute("SELECT marker FROM other").fetchone(), ("already committed to WAL",))
                    self.assertEqual(set(ITEMS.snapshot(copy)), {1001, 1002})
                self.assertEqual((backup / source.name).read_bytes(), b"original")
                self.assertEqual(ITEMS.snapshot(writer)[1001][2:4], ("new", 78))
                self.assertIn(1002, ITEMS.snapshot(writer))

                full = {1001: changed, 1003: (1, 3, "new row", *base[3:])}
                install_catalog(source, b"targeted", b"targeted", path, root / "backups", ITEMS, full)
                self.assertEqual(ITEMS.snapshot(writer), full)
                self.assertEqual(writer.execute("SELECT marker FROM other").fetchone(), ("already committed to WAL",))

    def test_file_publication_failure_restores_both_targets(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, path = root / "Cash.SData", root / "target.sqlite"
            catalog = CashCatalog(cash_file())
            source.write_bytes(catalog.raw)
            base, = catalog.products
            after = catalog.encode({0: replace(base, cost=24)})
            desired = product_rows(CashCatalog(after).products)
            with closing(connect(path)) as database, database:
                database.execute(f"CREATE TABLE {ITEMS.name}(ItemID,Type,TypeID)")
                database.execute(f"INSERT INTO {ITEMS.name} VALUES(1001,1,1)")
                columns = ", ".join(map(quote, PRODUCTS.columns))
                database.execute(f"CREATE TABLE {PRODUCTS.name}(RowID INTEGER,ProductCode TEXT PRIMARY KEY,{columns})")

            def fail_after_replace(file, raw):
                replace_file(file, raw)
                if raw == after:
                    raise OSError("simulated failure before SQLite commit")

            with patch("gx_items.install.replace_file", side_effect=fail_after_replace):
                with self.assertRaisesRegex(OSError, "simulated failure"):
                    install_catalog(source, catalog.raw, after, path, root / "backups", PRODUCTS, desired)
            self.assertEqual(source.read_bytes(), catalog.raw)
            with closing(read_database(path)) as database:
                self.assertEqual(PRODUCTS.snapshot(database), {})
            self.assertEqual(len(list((root / "backups").glob("*/rolled-back.json"))), 1)

            install_catalog(source, catalog.raw, after, path, root / "backups", PRODUCTS, desired)
            with closing(read_database(path)) as database:
                self.assertEqual(PRODUCTS.snapshot(database), desired)
