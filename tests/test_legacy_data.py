from pathlib import Path
from struct import pack, pack_into, unpack_from
from tempfile import TemporaryDirectory
import unittest

from gx_items import seed
from gx_items.binary_sdata import BinarySData
from gx_items.catalog import BODY_SIZE, Catalog
from gx_items.legacy_archive import LegacySource
from gx_items.legacy_conversion import convert_catalog, inspect_catalog, inspect_source
from gx_items.skill_catalog import NPCSkillCatalog, SKILL_FIELDS_BY_NAME, SkillCatalog


def string(raw):
    return pack("<I", len(raw)) + raw


def sah_file(name, offset, size):
    return string(name + b"\0") + pack("<qii", offset, size, 0)


def sah_folder(name, files=(), children=()):
    return (string(name + b"\0") + pack("<I", len(files)) + b"".join(files)
            + pack("<I", len(children)) + b"".join(children))


def archive(index, tree):
    index.write_bytes(b"SAH" + pack("<II", 0, 2) + bytes(40) + tree)
    index.with_suffix(".SAF").write_bytes(b"oldcurrent")


def skill_record(rank, size, name=b"", state=0):
    body = bytearray(size)
    body[0] = rank
    body[48 + (size == 138)] = state
    return string(name) + string(b"") + body


class LegacyDataTest(unittest.TestCase):
    def test_extracts_the_effective_archive_entry_and_retains_conflicting_folder(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / "data.sah"
            archive(index, sah_folder(b"", (
                sah_file(b"Items", 0, 3),
                sah_file(b"items", 3, 7),
            ), (sah_folder(b"Items", (sah_file(b"item.sdata", 0, 3),)),)))
            source = LegacySource.open(root)
            target = root / "result"
            self.assertEqual(source.extract(target), 2)
            self.assertEqual((target / "items.saf-file").read_bytes(), b"current")
            self.assertEqual((target / "Items" / "item.sdata").read_bytes(), b"old")
            with self.assertRaisesRegex(ValueError, "new output folder"):
                source.extract(target)

    def test_rejects_unsafe_paths_and_out_of_range_entries(self):
        with TemporaryDirectory() as tmp:
            index = Path(tmp) / "data.sah"
            archive(index, sah_folder(b"", (sah_file(b"..\\outside", 0, 1),)))
            with self.assertRaisesRegex(ValueError, "Invalid client path"):
                LegacySource.open(index)
            archive(index, sah_folder(b"", (sah_file(b"item.sdata", 3, 8),)))
            with self.assertRaisesRegex(ValueError, "Invalid SAF range"):
                LegacySource.open(index)

    def test_ep45_skill_expands_to_nine_slots_and_retains_state(self):
        raw = pack("<I", 1) + skill_record(1, 116, b"Silent", 3) + skill_record(0, 116) * 2
        converted, losses = convert_catalog("Skill.SData", raw, "4.5")
        catalog = SkillCatalog(converted)
        self.assertEqual(len(converted), len(raw) + 6 * 124)
        self.assertEqual(catalog.value((1, 1), SKILL_FIELDS_BY_NAME["StateType"]), 3)
        self.assertFalse(losses)

    def test_ep64_npc_skill_normalizes_the_native_rank_marker(self):
        raw = (pack("<I", 1) + skill_record(100, 138, b"Darkness", 4)
               + skill_record(0, 138) * 14)
        converted, losses = convert_catalog("NpcSkill.SData", raw, "6.4")
        catalog = NPCSkillCatalog(converted)
        self.assertEqual(catalog.value((1, 1), SKILL_FIELDS_BY_NAME["StateType"]), 4)
        self.assertEqual(losses["NPC skills with rank marker 100 normalized to rank 1"], 1)

    def test_detects_three_skill_layouts_and_preserves_shared_file_ambiguity(self):
        for episode, ranks, size in (("4.5", 3, 116), ("5.4", 9, 116), ("6.4", 15, 138)):
            raw = pack("<I", 1) + skill_record(1, size, b"Test") + skill_record(0, size) * (ranks - 1)
            catalog, detected = inspect_catalog("skill.sdata", raw)
            self.assertEqual(detected, episode)
            self.assertEqual(catalog.writable, episode == "5.4")
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "Skill.SData").write_bytes(raw)
                (root / "Cash.SData").write_bytes(bytes(4))
                layouts, detected = inspect_source(LegacySource.open(root))
                self.assertEqual(detected, episode)
                self.assertIsNone(layouts[Path("Cash.SData")])

    def test_reads_ep8_binary_table_with_shifted_checksum_and_alignment(self):
        fields = ("id", "name")
        plain = (bytes(128) + pack("<I", len(fields))
                 + b"".join(bytes((len(field),)) + field.encode("utf-16-le") for field in fields)
                 + pack("<Iq", 1, 41) + string("Dragón".encode()))
        encrypted = bytearray(seed.encode_like(seed.SIGNATURE.ljust(64, b"\0"), plain))
        checksum, size = unpack_from("<II", encrypted, 40)
        self.assertLess(size % 16, 15)
        pack_into("<III", encrypted, 40, 0, checksum, size + 1)
        table = BinarySData("DBMonsterText_USA.SData", bytes(encrypted))
        self.assertEqual(table.headers, fields)
        self.assertEqual([table.value(0, column) for column in range(2)], [41, "Dragón"])

    def test_reads_alternate_sah_file_counts_and_infers_ep8_from_catalogs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / "data.sah"
            tree = (string(b"\0") + pack("<I", 1 ^ 0x55)
                    + sah_file(b"DBMonsterText_USA.SData", 0, 3) + bytes(4))
            archive(index, tree)
            source = LegacySource.open(index)
            self.assertEqual(len(source.entries), 1)
            self.assertEqual(source.read_catalog(source.entries[0], 16), b"old")
            self.assertEqual(inspect_source(source)[1], "8")

    def test_ep64_items_keep_field_alignment_after_narrowing(self):
        record = bytearray(BODY_SIZE + 3 + 50)
        record[:2] = b"\x01\x01"
        pack_into("<IHBBH", record, 26, 75000, 10, 2, 3, 320)
        record[36] = 5
        record[-1] = 1
        raw = pack("<II", 1, 1) + string(b"Test") + string(b"") + record
        converted, losses = convert_catalog("Item.SData", raw, "6.4")
        catalog = Catalog(converted)
        item = catalog.items[1001]
        self.assertEqual(catalog.plain[item.body + 26:item.body + 34],
                         pack("<HHBBBB", 65535, 10, 2, 3, 255, 5))
        self.assertEqual(sum(losses.values()), 3)
        with self.assertRaisesRegex(ValueError, "source episodes"):
            convert_catalog("Item.SData", raw, "7")
