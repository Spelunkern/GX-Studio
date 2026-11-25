import unittest

import numpy as np

from gx_items.merchant_stock import MerchantItem, item_matches, item_types
from gx_items.model_render import camera_axes


class MerchantStockTests(unittest.TestCase):
    def test_weapon_filters_follow_faction_and_slot_count(self):
        light = MerchantItem("Sword", 12, "", 3, 2, 0, 0, False)
        dark = MerchantItem("Axe", 12, "", 3, 5, 0, 0, False)
        self.assertEqual(item_types(1, 1)[0], (1, "1-Hand Swords"))
        self.assertEqual(item_types(1, 2)[0], (3, "1-Hand Axes"))
        self.assertIsNone(item_types(1, 0)[0][0])
        self.assertTrue(item_matches(1, 1, 1, 3, 45, light))
        self.assertFalse(item_matches(1, 1, 1, 2, 45, light))
        self.assertFalse(item_matches(1, 2, 1, 3, 45, light))
        self.assertTrue(item_matches(1, 2, 3, 3, 47, dark))

    def test_generic_and_medicine_merchants_offer_different_items(self):
        fruit = MerchantItem("Fruit", 0, "", 0, 6, 0, 0, False)
        potion = MerchantItem("Potion", 0, "", 0, 6, 0, 0, True)
        self.assertEqual(item_types(0, 0)[0], (25, "Good Items 25 (type 25)"))
        self.assertTrue(item_matches(0, 0, 25, None, 25, fruit))
        self.assertFalse(item_matches(4, 0, 0, None, 25, fruit))
        self.assertTrue(item_matches(4, 0, 0, None, 25, potion))

    def test_npc_camera_starts_in_front_with_native_winding(self):
        axes = camera_axes("npc", 0, np.ones(3))
        self.assertAlmostEqual(float(axes[0, 2]), 0, places=5)
        self.assertLess(axes[2, 2], -.95)
        self.assertGreater(axes[0, 0], .95)
        self.assertAlmostEqual(float(np.linalg.det(axes)), -1, places=5)
