# Build and test GX Studio

GX Studio uses Python 3.11+ with Qt Widgets through PySide6. From the source
checkout, install the project in a virtual environment.

```sh
python -m venv .venv
# Activate the virtual environment for your shell.
python -m pip install .
python -m gx_items
python -m unittest discover -s tests
```

For headless Qt tests, set `QT_QPA_PLATFORM=offscreen`. Once installed,
`gx-studio` also starts the editor. To open a file with an explicit SQLite
target for the session, pass both paths.

```sh
python -m gx_items "/path/to/Client/data/Item/Item.SData" --sqlite "/path/to/Server/runtime/rapture.sqlite"
```

Use disposable copies when testing saves and installs. Unit fixtures exercise
catalog codecs, rank and record keys, transaction verification, backups and
recovery, model data readers and portable settings.

## Build a portable package

Build on the target operating system with Python 3.12 and the pinned packaging
dependencies. The macOS jobs use native runners for Apple silicon and Intel.
PyInstaller keeps the launcher, libraries and licenses together in one folder.

```sh
python -m pip install -r tools/release-requirements.txt .
python tools/package_studio.py
```

The output is `dist/GX Studio`. Use `--output` to create another folder.
Existing workspaces and backups are kept when choosing a new output.

`tools/release_archive.py` copies this output to a temporary location with
spaces and Unicode in the path, runs the bundled application with
`--check-startup` and creates the platform archive. For example:

```sh
python tools/release_archive.py linux-x64 v0.1.0-beta.1
```

## GitHub Releases

[The release workflow](../.github/workflows/release.yml) runs the same tests
and packaging steps on native Linux, Windows and macOS runners. A tag
beginning with `v` builds all four targets. After every target passes, the
workflow attaches the archives and `SHA256SUMS.txt` to a GitHub pre-release.
Manual runs produce workflow artifacts. Update `docs/beta-release.md` and
the version metadata before tagging a new beta.
