# PyInstaller build recipe for Snoopy.
#
#   python -m pip install -r requirements-build.txt
#   python -m PyInstaller --noconfirm Snoopy.spec
#
# Produces dist/Snoopy/: the Snoopy executable plus its _internal/ folder. Ship the
# whole folder. Snoopy keeps Conf/ and logs/ next to the executable (see Utils/Paths.py).
import sys

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

a = Analysis(
    ["SnoopyApp.py"],
    datas=[("images", "images")] + copy_metadata("keyring"),
    # keyring finds its backends (Windows Credential Manager, Secret Service, ...) at run time
    hiddenimports=collect_submodules("keyring.backends"),
    excludes=["Utils.TestApp", "Utils.TestRemote", "Utils.TestGUI", "Utils.FakeSSHServer", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Snoopy",
    console=False,  # a windowed app: no console window
    icon="images/SSIUtils.ico" if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Snoopy")
