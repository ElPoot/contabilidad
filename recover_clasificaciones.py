"""
Script de recuperación de clasificaciones dañadas.

El bug en heal_classified_path usaba upsert() que sobreescribía
estado/categoria/proveedor/etc con "" para los registros con rutas rotas.

Este script reconstruye esos campos desde la ruta_destino que SÍ quedó
guardada correctamente (apuntando a la carpeta renombrada).

USO:
    python recover_clasificaciones.py

Muestra qué registros reparará antes de hacer cambios.
Pide confirmación antes de escribir en la BD.
"""

import hashlib
import sqlite3
import sys
from pathlib import Path

# ── Localizar el .sqlite ──────────────────────────────────────────────────────
# Ajustar esta ruta al cliente afectado
SQLITE_PATHS = list(Path("Z:/DATA").rglob("clasificacion.sqlite")) if Path("Z:/DATA").exists() else []

VALID_ESTADOS = {"pendiente", "pendiente_pdf", "sin_xml", "clasificado"}
CATEGORIAS_CONOCIDAS = {"COMPRAS", "GASTOS", "OGND", "ACTIVO", "INGRESOS", "SIN_RECEPTOR"}


def reconstruct_from_path(ruta_destino: str) -> dict | None:
    """
    Dado Z:/DATA/PF-2026/Contabilidades/02-FEBRERO/CLIENTE/COMPRAS/PROVEEDOR/file.pdf
    reconstruye: estado, categoria, subtipo, nombre_cuenta, proveedor
    """
    p = Path(ruta_destino)
    if not p.exists():
        return None

    parts = p.parts
    try:
        cont_idx = next(i for i, x in enumerate(parts) if x == "Contabilidades")
        # parts[cont_idx+1]=mes, parts[cont_idx+2]=cliente, parts[cont_idx+3:]=resto
        if len(parts) <= cont_idx + 3:
            return None
        after_client = parts[cont_idx + 3:]   # (CATEGORIA, ...)
    except StopIteration:
        return None

    cat = after_client[0].upper() if after_client else ""
    if cat not in CATEGORIAS_CONOCIDAS:
        return None

    resultado = {
        "estado": "clasificado",
        "categoria": cat,
        "subtipo": "",
        "nombre_cuenta": "",
        "proveedor": "",
    }

    if cat == "COMPRAS" and len(after_client) >= 2:
        # COMPRAS / {proveedor} / archivo
        resultado["proveedor"] = after_client[1]

    elif cat == "GASTOS" and len(after_client) >= 2:
        # GASTOS / {subtipo} / {nombre_cuenta} / {proveedor} / archivo
        resultado["subtipo"] = after_client[1] if len(after_client) > 1 else ""
        resultado["nombre_cuenta"] = after_client[2] if len(after_client) > 2 else ""
        resultado["proveedor"] = after_client[3] if len(after_client) > 3 else ""

    elif cat == "OGND" and len(after_client) >= 2:
        # OGND / {subtipo} / archivo
        resultado["subtipo"] = after_client[1]

    elif cat == "ACTIVO" and len(after_client) >= 2:
        # ACTIVO / {proveedor} / archivo
        resultado["proveedor"] = after_client[1]

    return resultado


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def recover(db_path: Path, dry_run: bool = True) -> int:
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Procesando: {db_path}")
    reparados = 0
    sin_ruta = 0
    sin_archivo = 0
    ya_ok = 0

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM clasificaciones").fetchall()

        for row in rows:
            clave = row["clave_numerica"]
            estado = row["estado"] or ""
            ruta = row["ruta_destino"] or ""

            # Solo reparar registros dañados (estado inválido)
            if estado in VALID_ESTADOS:
                ya_ok += 1
                continue

            if not ruta:
                sin_ruta += 1
                continue

            if not Path(ruta).exists():
                sin_archivo += 1
                continue

            reconstruido = reconstruct_from_path(ruta)
            if not reconstruido:
                print(f"  ⚠ No se pudo reconstruir categoria de: {ruta}")
                continue

            sha = sha256_file(Path(ruta))

            print(
                f"  ✔ {clave[:20]}... → estado=clasificado "
                f"cat={reconstruido['categoria']} prov={reconstruido['proveedor'][:30]}"
            )

            if not dry_run:
                conn.execute(
                    """UPDATE clasificaciones SET
                        estado=?, categoria=?, subtipo=?, nombre_cuenta=?,
                        proveedor=?, sha256=?
                       WHERE clave_numerica=?""",
                    (
                        reconstruido["estado"],
                        reconstruido["categoria"],
                        reconstruido["subtipo"],
                        reconstruido["nombre_cuenta"],
                        reconstruido["proveedor"],
                        sha,
                        clave,
                    ),
                )
            reparados += 1

        if not dry_run:
            conn.commit()

    print(f"\n  Registros ya correctos:     {ya_ok}")
    print(f"  Registros sin ruta:         {sin_ruta}")
    print(f"  Registros ruta no existe:   {sin_archivo}")
    print(f"  Registros {'a reparar' if dry_run else 'reparados'}:      {reparados}")
    return reparados


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not SQLITE_PATHS:
        print("No se encontraron archivos clasificacion.sqlite en Z:/DATA")
        print("Asegúrese de que la unidad Z: esté disponible.")
        sys.exit(1)

    print(f"Se encontraron {len(SQLITE_PATHS)} base(s) de datos:\n")
    for i, p in enumerate(SQLITE_PATHS):
        print(f"  [{i}] {p}")

    print("\nIngrese el número de la BD a recuperar (o 'todas' para procesar todas):")
    sel = input("> ").strip().lower()

    if sel == "todas":
        seleccionadas = SQLITE_PATHS
    else:
        try:
            seleccionadas = [SQLITE_PATHS[int(sel)]]
        except (ValueError, IndexError):
            print("Selección inválida.")
            sys.exit(1)

    # DRY RUN primero
    print("\n" + "="*60)
    print("VISTA PREVIA (sin cambios):")
    print("="*60)
    total = sum(recover(p, dry_run=True) for p in seleccionadas)

    if total == 0:
        print("\nNo hay registros dañados. No se necesita recuperación.")
        sys.exit(0)

    print(f"\nTotal de registros a reparar: {total}")
    print("\n¿Aplicar los cambios? (escriba 'si' para confirmar):")
    confirmacion = input("> ").strip().lower()

    if confirmacion == "si":
        print("\n" + "="*60)
        print("APLICANDO CAMBIOS:")
        print("="*60)
        for p in seleccionadas:
            recover(p, dry_run=False)
        print("\n✔ Recuperación completada. Recargue el cliente en la app.")
    else:
        print("Cancelado. No se realizaron cambios.")
