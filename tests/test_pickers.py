import unittest

from gx_items.pickers import ItemEntry, ItemTable


class ItemTableTest(unittest.TestCase):
    def test_enchant_list_keeps_only_named_gx_consumables(self):
        valid = (25043, 44022, 100001, 101001, 102001)
        entries = [ItemEntry(item_id, "Material") for item_id in reversed(valid)]
        entries.extend((
            ItemEntry(0, "No material"),
            ItemEntry(1001, "Long Sword"),
            ItemEntry(29001, "Quest item"),
            ItemEntry(30001, "Lapis"),
            ItemEntry(38001, "Helmet"),
            ItemEntry(94001, "Coins"),
            ItemEntry(25044, "???44"),
            ItemEntry(25045, ""),
        ))
        table = ItemTable(entries, None, None, materials_only=True)
        self.assertEqual(tuple(entry.item_id for entry in table.entries), valid)

    def test_cash_picker_keeps_every_family_and_high_ids(self):
        entries = [ItemEntry(150001, "Costume"), ItemEntry(1001, "Sword"), ItemEntry(30001, "Lapis"),
                   ItemEntry(25001, "Potion"), ItemEntry(29001, "Quest item")]
        table = ItemTable(entries, None, None)
        self.assertEqual(tuple(entry.item_id for entry in table.entries), (1001, 25001, 29001, 30001, 150001))
