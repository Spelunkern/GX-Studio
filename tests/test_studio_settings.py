from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gx_items.studio_settings import CLIENT_ROOT_KEY, DATABASE_KEY, SERVER_FOLDER_KEY
from gx_items.studio_settings import open_settings, remember_path, workspace_path


class StudioSettingsTest(unittest.TestCase):
    def test_relative_workspace_paths_follow_the_portable_folder(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            studio = root / "Studio"
            external = root / "base externa ñ.sqlite"
            settings = open_settings(studio)
            remember_path(settings, CLIENT_ROOT_KEY, studio / "Cliente ñ" / "data", studio)
            remember_path(settings, SERVER_FOLDER_KEY, studio / "GX Server", studio)
            remember_path(settings, DATABASE_KEY, external, studio)
            settings.sync()
            del settings
            relocated = studio.rename(root / "Studio movido")

            settings = open_settings(relocated)
            self.assertEqual(workspace_path(settings, CLIENT_ROOT_KEY, relocated), relocated / "Cliente ñ" / "data")
            self.assertEqual(workspace_path(settings, SERVER_FOLDER_KEY, relocated), relocated / "GX Server")
            self.assertEqual(workspace_path(settings, DATABASE_KEY, relocated), external)
            remember_path(settings, DATABASE_KEY, None, relocated)
            self.assertIsNone(workspace_path(settings, DATABASE_KEY, relocated))
