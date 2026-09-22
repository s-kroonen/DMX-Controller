# PyInstaller spec for the packaged desktop app.
#
# Build (from the repo root, both backend/requirements.txt and
# desktop/requirements.txt installed into the active venv):
#   pyinstaller desktop/dmx_controller.spec
#
# Output: dist/DMX Controller/DMX Controller.exe (onedir -- faster startup
# and easier to debug than onefile; the frontend/profile data sits next to
# the exe instead of being re-extracted to a temp dir on every launch).

from pathlib import Path

block_cipher = None

REPO_ROOT = Path(SPECPATH).resolve().parent
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"

datas = [
    (str(FRONTEND_DIR), "frontend"),
    (str(BACKEND_DIR / "app" / "fixtures" / "profiles"), "app/fixtures/profiles"),
]

# uvicorn resolves several of its own implementations dynamically
# (protocol/loop backends chosen by what's installed), which PyInstaller's
# static import analysis can miss -- a well-known uvicorn+PyInstaller gotcha.
hiddenimports = [
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
]

a = Analysis(
    [str(REPO_ROOT / "desktop" / "launcher.py")],
    pathex=[str(BACKEND_DIR), str(REPO_ROOT / "desktop")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DMX Controller",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # no console window -- this is a GUI app
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DMX Controller",
)
