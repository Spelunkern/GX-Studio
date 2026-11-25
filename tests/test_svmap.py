from dataclasses import replace
from struct import pack
import unittest

from gx_items.svmap import ServerMap


class ServerMapTest(unittest.TestCase):
    def test_reads_all_gx_sections_and_xz_mask_order(self):
        raw = (
            pack("<i", 8) + bytes((0, 0b100, 0, 0, 0, 0, 0, 0)) + pack("<i", 4)
            + pack("<i3f", 1, 1, 2, 3)
            + pack("<i6fiII", 1, 0, 1, 2, 3, 4, 5, 1, 61, 3)
            + pack("<iiii4f", 1, 2, 17, 1, 3, 7, 6, -1.5)
            + pack("<i3fiHHI3f", 1, 1, 2, 3, -1, 5, 20, 42, 7, 8, 9)
            + pack("<iiii6f", 1, 1, 2, 999, 0, 1, 2, 3, 4, 5)
            + pack("<i6fii", 1, 0, 1, 2, 3, 4, 5, 301, 302)
        )
        data = ServerMap.read(raw)
        self.assertTrue(data.blocked(2, 1))
        self.assertFalse(data.blocked(1, 2))
        self.assertEqual((data.size, data.cell_size, data.trailing_bytes), (8, 4, 0))
        self.assertEqual(data.ladders, ((1, 2, 3),))
        self.assertEqual(data.monsters[0].spawns, ((61, 3),))
        self.assertEqual(data.npcs[0].patrol, ((3, 7, 6, -1.5),))
        self.assertEqual(data.portals[0][3:], (-1, 5, 20, 42, 7, 8, 9))
        self.assertEqual((data.spawns[0].faction, data.spawns[0].auxiliary), (2, 999))
        self.assertEqual(data.named[0][-2:], (301, 302))
        self.assertEqual(data.encode(), raw)
        padded = ServerMap.read(raw + b"extra")
        npc = replace(padded.npcs[0], patrol=(*padded.npcs[0].patrol, (3, 7, 8, 0)))
        updated = ServerMap.read(replace(padded, npcs=(npc,)).encode())
        self.assertEqual(updated.npcs, (npc,))
        self.assertEqual((updated.mask, updated.tail), (data.mask, b"extra"))
        with self.assertRaisesRegex(ValueError, "Invalid named area count"):
            ServerMap.read(raw[:-1])

    def test_rejects_impossible_counts_before_allocating_records(self):
        raw = pack("<i", 8) + bytes(8) + pack("<ii", 0, 2**31 - 1)
        with self.assertRaisesRegex(ValueError, "Invalid ladder count"):
            ServerMap.read(raw, "0.svmap")
