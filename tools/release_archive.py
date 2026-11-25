from argparse import ArgumentParser
import os
from pathlib import Path
import shutil
from subprocess import run
import sys
from tempfile import TemporaryDirectory


def main():
    parser = ArgumentParser(description="Verify a relocated build and create a release archive.")
    parser.add_argument("target")
    parser.add_argument("version")
    args = parser.parse_args()
    directory = Path("dist").resolve()
    portable = directory / "GX Studio"
    with TemporaryDirectory(prefix="GX Studio test ñ ") as temporary:
        relocated = Path(temporary) / portable.name
        shutil.copytree(portable, relocated, symlinks=True)
        launcher = {"win32": "GX Studio.exe", "darwin": "GX Studio.app/Contents/MacOS/GX Studio"}.get(
            sys.platform, "GX Studio",
        )
        run([str(relocated / launcher), "--check-startup"], cwd=temporary, timeout=45, check=True,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    basename = directory / f"gx-studio-{args.version}-{args.target}"
    shutil.make_archive(str(basename), "zip" if sys.platform == "win32" else "gztar",
                        root_dir=directory, base_dir=portable.name)


if __name__ == "__main__":
    main()
