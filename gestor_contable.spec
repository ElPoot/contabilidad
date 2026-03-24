# gestor_contable.spec
# ─────────────────────────────────────────────────────────────────────────────
# Archivo de configuración para PyInstaller.
#
# Uso normal (via build.py — recomendado):
#   python build.py
#
# Uso manual:
#   pyinstaller gestor_contable.spec --noconfirm
#
# El ejecutable queda en:
#   dist/GestorContable-v{version}/GestorContable.exe
# ─────────────────────────────────────────────────────────────────────────────

import sys
import importlib.util
from pathlib import Path
import customtkinter
import fitz  # pymupdf

# ── Leer versión desde version.py sin importar el paquete completo ─────────────
SPEC_DIR  = Path(SPECPATH)
SRC_DIR   = SPEC_DIR

_version_spec = importlib.util.spec_from_file_location(
    "version",
    str(SRC_DIR / "gestor_contable" / "version.py"),
)
_version_mod = importlib.util.module_from_spec(_version_spec)
_version_spec.loader.exec_module(_version_mod)

APP_VERSION = _version_mod.__version__
APP_NAME    = _version_mod.__app_name__
DIST_NAME   = f"GestorContable-v{APP_VERSION}"

print(f"[build] Version: {APP_VERSION}  ->  dist/{DIST_NAME}/")

# ── Rutas de paquetes ──────────────────────────────────────────────────────────
CTK_DIR  = Path(customtkinter.__file__).parent
FITZ_DIR = Path(fitz.__file__).parent

# ── Archivo de versión para los metadatos del .exe (Windows VERSIONINFO) ──────
VERSION_FILE = str(SRC_DIR / "build_version_info.txt")

v = tuple(int(x) for x in APP_VERSION.split("."))
with open(VERSION_FILE, "w") as _f:
    _f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({v[0]}, {v[1]}, {v[2]}, 0),
    prodvers=({v[0]}, {v[1]}, {v[2]}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040C04B0',
        [StringStruct(u'CompanyName',      u'{APP_NAME}'),
         StringStruct(u'FileDescription',  u'{APP_NAME}'),
         StringStruct(u'FileVersion',      u'{APP_VERSION}'),
         StringStruct(u'ProductName',      u'{APP_NAME}'),
         StringStruct(u'ProductVersion',   u'{APP_VERSION}'),
         StringStruct(u'LegalCopyright',   u'Clasificador Contable CR'),
        ])
    ]),
    VarFileInfo([VarStruct(u'Translation', [1036, 1200])])
  ]
)
""")

# ── Análisis ──────────────────────────────────────────────────────────────────
a = Analysis(
    [str(SRC_DIR / "gestor_contable" / "main.py")],
    pathex=[str(SRC_DIR)],
    binaries=[],
    datas=[
        (str(CTK_DIR), "customtkinter"),
        (str(FITZ_DIR), "fitz"),
    ],
    hiddenimports=[
        "customtkinter",
        "customtkinter.windows",
        "customtkinter.windows.widgets",
        "customtkinter.windows.widgets.appearance_mode",
        "customtkinter.windows.widgets.scaling",
        "customtkinter.windows.widgets.font",
        "customtkinter.windows.widgets.image",
        "PIL",
        "PIL.Image",
        "PIL.ImageTk",
        "fitz",
        "fitz.fitz",
        "pandas",
        "openpyxl",
        "openpyxl.styles",
        "openpyxl.utils",
        "openpyxl.workbook",
        "openpyxl.worksheet",
        "cryptography",
        "cryptography.hazmat.primitives",
        "cryptography.hazmat.backends",
        "cryptography.hazmat.backends.openssl",
        "cryptography.hazmat.bindings._rust",
        "keyring",
        "keyring.backends",
        "keyring.backends.Windows",
        "keyring.backends.fail",
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "sqlite3",
        "logging.handlers",
        "xml.etree.ElementTree",
        "ctypes",
        "ctypes.wintypes",
        "subprocess",
        "threading",
        "concurrent.futures",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "numpy",
        "scipy",
        "IPython",
        "jupyter",
        "pytest",
        "setuptools",
        "pip",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GestorContable",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    version=VERSION_FILE,
    # icon="gestor_contable/assets/icon.ico",  # descomentar cuando tengas ícono
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=DIST_NAME,   # carpeta con versión: dist/GestorContable-v1.0.0/
)
