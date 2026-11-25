from argparse import ArgumentParser
from importlib.metadata import distribution
from pathlib import Path
import shutil
from subprocess import run
import sys


def main():
    source = Path(__file__).resolve().parent.parent
    app_name = "GX Studio"
    parser = ArgumentParser(description="Build a portable GX Studio folder for this platform.")
    parser.add_argument("--output", type=Path, default=source / "dist" / app_name)
    portable = parser.parse_args().output.resolve()
    if portable.exists():
        parser.error(f"Choose a new output folder to preserve the existing workspace at {portable}")
    work = source / "build" / "studio-package"
    mac = sys.platform == "darwin"
    run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
        "--name", app_name, "--paths", str(source),
        "--icon", str(source / "gx_items/resources/workspace.ico"),
        "--add-data", f"{source / 'gx_items/resources'}:gx_items/resources",
        "--distpath", str(work / "dist"), "--workpath", str(work), "--specpath", str(work),
        str(source / "tools/studio_entry.py"),
    ], cwd=source, check=True)
    suffix = ".app" if mac else ""
    built = work / "dist" / (app_name + suffix)
    portable.mkdir(parents=True)
    destination = portable / built.name if mac else portable
    shutil.copytree(built, destination, symlinks=True, dirs_exist_ok=True)
    portable = destination.parent if mac else destination
    notices = portable / "licenses"
    shutil.copytree(source / "licenses", notices, dirs_exist_ok=True)
    for library in ("numpy", "Pillow", "psutil", "pyinstaller"):
        package = distribution(library)
        for entry in package.files or ():
            if ".dist-info" in entry.parts[0] and entry.name.upper().startswith(("LICENSE", "COPYING")):
                target = notices / library / entry.name
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(package.locate_file(entry), target)
    for name in ("LICENSE", "NOTICE.md", "README.md"):
        shutil.copy2(source / name, portable)
    print(portable)


if __name__ == "__main__":
    main()
