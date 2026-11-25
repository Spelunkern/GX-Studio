from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gx_items.safe_zones import SafeZones


class SafeZonesTest(unittest.TestCase):
    def test_edit_preserves_other_maps_and_collapses_modified_duplicate_sections(self):
        raw = (
            b"; header\r\n[Camp]\r\nMap=45\r\nProtectedFaction=Light\r\n"
            b"Vertices=(0, 0) (2, 0) (2, 2) (0, 2)\r\n\r\n"
            b"[Other]\r\nMap=46\r\nProtectedFaction=Both\r\n"
            b"Vertices=(1, 1) (3, 1) (3, 3) (1, 3)\r\n\r\n"
            b"[CAMP]\r\nProtectedFaction=Fury\r\n"
        )
        original = SafeZones(raw)
        self.assertEqual(original.for_map(45)[0].faction, "Fury")
        self.assertEqual(original.encode_map(45, original.for_map(45)), raw)
        zone = replace(original.for_map(45)[0], name="New Camp", faction="Both")
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / "SafeZones.INI"
            path.write_bytes(raw)
            saved, backup = original.save(path, 45, (zone,), directory / "backups")
            self.assertEqual(saved.for_map(45), (zone,))
            self.assertEqual(saved.for_map(46), original.for_map(46))
            self.assertIn(b"[Other]\r\nMap=46\r\nProtectedFaction=Both", saved.raw)
            self.assertEqual(backup.read_bytes(), raw)
            with self.assertRaisesRegex(ValueError, "changed on disk"):
                original.save(path, 45, (), directory / "backups")

    def test_rejects_crossed_vertices_and_duplicate_names(self):
        data = SafeZones(b"[Camp]\nMap=45\nProtectedFaction=Both\nVertices=0 0 2 0 2 2 0 2\n")
        zone = data.zones[0]
        with self.assertRaisesRegex(ValueError, "convex"):
            data.encode_map(45, (replace(zone, points=(zone.points[0], zone.points[2], zone.points[1], zone.points[3])),))
        with self.assertRaisesRegex(ValueError, "unique"):
            data.encode_map(46, (replace(zone, map_id=46),))
