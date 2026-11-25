# Third-party notices

GX Studio is distributed under the BSD 3-Clause License. Third-party material
retains the terms listed here and in the `licenses/` directory.

- [shStudio](https://github.com/1ly4337/shStudio) is the interface reference.
  GX Studio uses its program icon (resource 105) and Grades graphic (160).
  See [the MIT notice](licenses/shStudio.txt).
- [Shaiya GX](https://github.com/Spelunkern/Shaiya-GX) supplies the GX EP5
  format and schema reference and the SEED implementation adapted here.
  The original Parsec implementation and Shaiya GX notices are included
  in [licenses/](licenses/).
- Portable builds dynamically link Qt 6.8.3 and Qt for Python 6.8.3.
  These are covered by the included LGPL v3 and GPL v3 texts. The binaries
  come from the unmodified PySide6 distribution. Corresponding sources are
  available from the [Qt 6.8.3 source archives](https://download.qt.io/archive/qt/6.8/6.8.3/single/)
  and [Qt for Python 6.8.3 sources](https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.8.3).
  Qt also documents [its third-party components](https://doc.qt.io/qt-6.8/licenses-used-in-qt.html).
  You may replace the shared libraries with compatible builds. On macOS,
  a modified bundle may need to be ad hoc signed again to run.
- Qt Multimedia uses FFmpeg where bundled by the Qt distribution.
  See the included LGPL v2.1 text and
  [Qt's FFmpeg attribution and source links](https://doc.qt.io/qt-6.8/qtmultimedia-attribution-ffmpeg.html).
- The Python runtime is covered by the
  [Python Software Foundation License](https://docs.python.org/3.12/license.html).
  The distribution also includes the applicable notices from NumPy,
  Pillow, psutil and PyInstaller.
- Gallery images are captures of GX Studio using synthetic demo data, including
  original example icons and a practice sword model. Bundles contain no game
  catalogs, personal SQLite databases or original Shaiya game assets.
