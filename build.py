"""
build.py — Script de release para Gestor Contable
──────────────────────────────────────────────────
Uso:
    python build.py            # sube PATCH (1.0.0 → 1.0.1)
    python build.py minor      # sube MINOR (1.0.1 → 1.1.0)
    python build.py major      # sube MAJOR (1.1.0 → 2.0.0)
    python build.py 1.2.3      # versión específica
    python build.py --no-tag   # sin crear tag de git
"""

from __future__ import annotations

import io
import sys

# Forzar UTF-8 en consola Windows para evitar UnicodeEncodeError
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import argparse
import importlib.util
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from datetime import datetime

# ── Rutas ────────────────────────────────────────────────────────────────────
ROOT         = Path(__file__).parent
VERSION_FILE = ROOT / "gestor_contable" / "version.py"
SPEC_FILE    = ROOT / "gestor_contable.spec"
DIST_DIR     = ROOT / "dist"
RELEASES_DIR = ROOT / "releases"
CHANGELOG    = ROOT / "CHANGELOG.md"


# ── Colores en consola ────────────────────────────────────────────────────────
def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m"

OK   = lambda t: print(_c(f"  ✓  {t}", "32"))
WARN = lambda t: print(_c(f"  ⚠  {t}", "33"))
ERR  = lambda t: print(_c(f"  ✗  {t}", "31"))
HDR  = lambda t: print(_c(f"\n{'─'*50}\n  {t}\n{'─'*50}", "36"))
INFO = lambda t: print(f"     {t}")


# ── Versión ───────────────────────────────────────────────────────────────────
def _load_version() -> tuple[int, int, int]:
    spec = importlib.util.spec_from_file_location("version", VERSION_FILE)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    parts = mod.__version__.split(".")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def _bump(current: tuple[int, int, int], bump_type: str) -> tuple[int, int, int]:
    ma, mi, pa = current
    if bump_type == "major": return (ma + 1, 0, 0)
    if bump_type == "minor": return (ma, mi + 1, 0)
    return (ma, mi, pa + 1)  # patch


def _parse_version(raw: str) -> tuple[int, int, int]:
    parts = raw.strip().lstrip("v").split(".")
    if len(parts) != 3:
        raise ValueError(f"Formato inválido: {raw!r}  (esperado: MAJOR.MINOR.PATCH)")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def _write_version(v: tuple[int, int, int]) -> str:
    ver_str = ".".join(str(x) for x in v)
    content = VERSION_FILE.read_text(encoding="utf-8")
    new_content = "\n".join(
        f'__version__ = "{ver_str}"' if line.startswith("__version__") else line
        for line in content.splitlines()
    )
    VERSION_FILE.write_text(new_content, encoding="utf-8")
    return ver_str


# ── Git ───────────────────────────────────────────────────────────────────────
def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, check=check)


def _git_ok() -> bool:
    r = _git("status", "--porcelain", check=False)
    return r.returncode == 0


def _has_uncommitted() -> bool:
    r = _git("status", "--porcelain")
    return bool(r.stdout.strip())


def _create_tag(version: str, message: str) -> None:
    tag = f"v{version}"
    _git("tag", "-a", tag, "-m", message)
    OK(f"Tag creado: {tag}")


# ── Changelog ─────────────────────────────────────────────────────────────────
def _update_changelog(version: str, notes: str) -> None:
    date = datetime.today().strftime("%Y-%m-%d")
    entry = f"\n## v{version} — {date}\n\n{notes}\n"

    if CHANGELOG.exists():
        existing = CHANGELOG.read_text(encoding="utf-8")
        # Evitar duplicado si la versión ya está registrada
        if f"## v{version}" in existing:
            WARN(f"v{version} ya está en CHANGELOG.md — omitiendo entrada duplicada")
            return
        lines = existing.splitlines(keepends=True)
        insert_at = 1
        for i, line in enumerate(lines):
            if line.startswith("## "):
                insert_at = i
                break
        lines.insert(insert_at, entry)
        CHANGELOG.write_text("".join(lines), encoding="utf-8")
    else:
        CHANGELOG.write_text(
            f"# Changelog — Gestor Contable\n{entry}",
            encoding="utf-8",
        )
    OK(f"CHANGELOG.md actualizado")


# ── PyInstaller ───────────────────────────────────────────────────────────────
def _build_exe() -> Path:
    HDR("Compilando con PyInstaller...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC_FILE), "--noconfirm"],
        cwd=ROOT,
    )
    if result.returncode != 0:
        ERR("PyInstaller falló. Revisa los mensajes arriba.")
        sys.exit(1)

    # Encontrar la carpeta generada (dist/GestorContable-vX.Y.Z/)
    candidates = sorted(DIST_DIR.glob("GestorContable-v*"))
    if not candidates:
        ERR("No se encontró la carpeta de salida en dist/")
        sys.exit(1)
    return candidates[-1]


# ── ZIP de release ────────────────────────────────────────────────────────────
def _create_zip(source_dir: Path, version: str) -> Path:
    RELEASES_DIR.mkdir(exist_ok=True)
    zip_path = RELEASES_DIR / f"GestorContable-v{version}.zip"

    INFO(f"Empaquetando → {zip_path.name}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file in source_dir.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(source_dir.parent))

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    OK(f"ZIP creado: {zip_path.name}  ({size_mb:.1f} MB)")
    return zip_path


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Build y release de Gestor Contable")
    parser.add_argument(
        "bump",
        nargs="?",
        default="patch",
        help="Tipo de bump: patch (default), minor, major, o versión exacta (ej: 1.2.3)",
    )
    parser.add_argument("--no-tag",    action="store_true", help="No crear tag de git")
    parser.add_argument("--no-commit", action="store_true", help="No hacer commit automático")
    parser.add_argument("--no-zip",    action="store_true", help="No crear ZIP de release")
    args = parser.parse_args()

    # ── Estado inicial ────────────────────────────────────────────────────────
    HDR("Gestor Contable — Build")
    current = _load_version()
    current_str = ".".join(str(x) for x in current)
    INFO(f"Versión actual: {current_str}")

    # ── Calcular nueva versión ────────────────────────────────────────────────
    if args.bump in ("patch", "minor", "major"):
        new_v = _bump(current, args.bump)
    else:
        try:
            new_v = _parse_version(args.bump)
        except ValueError as e:
            ERR(str(e))
            sys.exit(1)

    new_str = ".".join(str(x) for x in new_v)
    INFO(f"Nueva versión:  {new_str}")

    if new_v == current:
        WARN("La versión no cambió. Usa 'minor', 'major' o especifica una versión mayor.")
        confirm = input("     ¿Continuar de todas formas? [s/N] ").strip().lower()
        if confirm != "s":
            sys.exit(0)

    # ── Notas de release ──────────────────────────────────────────────────────
    print("\n     Describe los cambios de esta versión (Enter en blanco para terminar):")
    notes_lines = []
    while True:
        line = input("     > ")
        if not line:
            break
        notes_lines.append(f"- {line}")
    notes = "\n".join(notes_lines) if notes_lines else "- Actualización de la aplicación."

    # ── Confirmación ──────────────────────────────────────────────────────────
    print()
    INFO(f"  Versión:   {current_str}  →  {new_str}")
    INFO(f"  Tag git:   {'no' if args.no_tag else f'v{new_str}'}")
    INFO(f"  ZIP:       {'no' if args.no_zip else f'releases/GestorContable-v{new_str}.zip'}")
    print()
    confirm = input("     ¿Proceder? [S/n] ").strip().lower()
    if confirm == "n":
        INFO("Cancelado.")
        sys.exit(0)

    # ── Verificar git ─────────────────────────────────────────────────────────
    git_available = _git_ok()
    if git_available and _has_uncommitted() and not args.no_commit:
        WARN("Hay cambios sin commitear. Se commitearán junto con el bump de versión.")

    # ── Actualizar version.py ─────────────────────────────────────────────────
    HDR("Actualizando versión...")
    _write_version(new_v)
    OK(f"version.py → {new_str}")

    # ── Actualizar CHANGELOG ──────────────────────────────────────────────────
    _update_changelog(new_str, notes)

    # ── Commit de versión ─────────────────────────────────────────────────────
    if git_available and not args.no_commit:
        HDR("Commit de versión...")
        _git("add", str(VERSION_FILE), str(CHANGELOG))
        _git("commit", "-m", f"Release v{new_str}")
        OK(f"Commit: Release v{new_str}")

    # ── Tag de git ────────────────────────────────────────────────────────────
    if git_available and not args.no_tag:
        _create_tag(new_str, f"Release v{new_str}\n\n{notes}")

    # ── Compilar ──────────────────────────────────────────────────────────────
    dist_folder = _build_exe()
    OK(f"Ejecutable: {dist_folder}")

    # ── ZIP ───────────────────────────────────────────────────────────────────
    if not args.no_zip:
        zip_path = _create_zip(dist_folder, new_str)

    # ── Resumen ───────────────────────────────────────────────────────────────
    HDR("Release completado")
    OK(f"Versión:     v{new_str}")
    OK(f"Ejecutable:  dist/{dist_folder.name}/GestorContable.exe")
    if not args.no_zip:
        OK(f"ZIP:         releases/GestorContable-v{new_str}.zip")
    if git_available and not args.no_tag:
        OK(f"Tag git:     v{new_str}")
    print()
    INFO("Para compartir: copia la carpeta dist/ o el ZIP de releases/")
    print()


if __name__ == "__main__":
    main()
