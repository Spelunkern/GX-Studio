from pathlib import Path
import sys

from PySide6.QtCore import QSettings, QStandardPaths

LEGACY_SOURCE_KEY = "legacy/lastData"
CLIENT_ROOT_KEY = "workspace/clientData"
SERVER_FOLDER_KEY = "workspace/serverFolder"
DATABASE_KEY = "workspace/database"


def studio_directory() -> Path:
    if getattr(sys, "frozen", False):
        directory = Path(sys.executable).resolve().parent
        if directory.name == "MacOS" and directory.parent.parent.suffix == ".app":
            return directory.parent.parent.parent
        return directory
    source = Path(__file__).resolve().parent.parent
    if (source / "pyproject.toml").is_file():
        return source
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)).resolve()


def open_settings(directory: Path) -> QSettings:
    filename = directory / "studio.ini"
    settings = QSettings(str(filename), QSettings.Format.IniFormat)
    if not filename.exists() and (directory / "pyproject.toml").is_file() and not getattr(sys, "frozen", False):
        previous = QSettings(
            QSettings.Format.IniFormat, QSettings.Scope.UserScope, "GX Data Editor", "GX Data Editor",
        )
        for key in previous.allKeys():
            settings.setValue(key, previous.value(key))
        settings.sync()
    return settings


def workspace_path(settings: QSettings, key: str, directory: Path) -> Path | None:
    value = settings.value(key, "", type=str)
    return (directory / value).resolve() if value else None


def remember_path(settings: QSettings, key: str, path: Path | None, directory: Path):
    if path is None:
        settings.remove(key)
    else:
        path = path.resolve()
        directory = directory.resolve()
        settings.setValue(key, path.relative_to(directory).as_posix() if path.is_relative_to(directory) else str(path))
