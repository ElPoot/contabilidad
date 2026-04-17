---
name: audit-path-sanitize
description: Revisa la curacion de carpetas vacias, rutas rotas y nombres renombrados en el flujo contable. Detecta path healing incorrecto, borrado de carpetas validas y renames incompletos en SQLite. Usar esta skill cuando mencionas sanitizar rutas, carpetas vacias, rename cliente, path healing, _sanitize_folder, folder sanitizer, reparar rutas, carpetas residuales, orphan recovery, rutas en SQLite.
---

# Auditoria: Saneamiento y Reparacion de Rutas

Sos un auditor especializado en la integridad de las rutas del sistema contable. El sistema tiene tres fuentes de rutas que pueden desincronizarse: el filesystem, la SQLite de clasificacion y los nombres de cliente en `client_profiles.json`.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas que las funciones de saneamiento son peligrosas. Verifica que cada operacion destructiva (rmdir, rename) tenga las validaciones correctas.

## Alcance y limites

Este skill audita SOLO el saneamiento de rutas y carpetas:
- `gestor_contable/core/folder_sanitizer.py` -- limpieza de carpetas vacias
- `gestor_contable/core/classifier.py` -- `_sanitize_folder()`
- `gestor_contable/core/classification_utils.py` -- deteccion de huerfanos

**Fuera de alcance** (lo cubren otros skills):
- Heal de cliente y renombrado de carpeta --> audit-client-session
- Construccion de ruta de destino para clasificacion --> audit-accounting-classify
- Locking de SQLite al actualizar rutas --> audit-sqlite

## Paso 1: Verificar _sanitize_folder

```
Buscar en classifier.py:
1. "_sanitize_folder" -- implementacion completa
2. "replace\(|strip\(\)" -- que caracteres reemplaza o elimina
3. "[<>:\"/\\|?*]" -- chars invalidos en Windows
```

## Paso 2: Verificar saneamiento de carpetas vacias

```
Buscar en folder_sanitizer.py:
1. "rmdir|rmtree|os\.rmdir" -- eliminacion de carpetas
2. "iterdir\(\)|listdir|is_empty" -- verificacion de que esta vacia antes de borrar
3. "COMPRAS|GASTOS|ACTIVO|OGND" -- carpetas estructurales protegidas?
```

## Paso 3: Verificar deteccion de huerfanos

```
Buscar en classification_utils.py:
1. "orphan|huerfano|sin_carpeta" -- deteccion de rutas rotas
2. "dest_path.*exists|exists.*dest" -- verificacion de que el destino aun existe
```

## Paso 4: Generar reporte

```
AUDITORIA: SANEAMIENTO DE RUTAS
=================================

Archivos revisados: [lista de archivos que realmente leiste]

FUNCIONES VERIFICADAS
----------------------
_sanitize_folder(): [chars cubiertos: lista / gaps: lista]
folder_sanitizer rmdir: [verifica vacio: SI/NO / protege estructurales: SI/NO]
deteccion de huerfanos: [IMPLEMENTADA / NO]

HALLAZGOS
---------
[Solo problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema
  Evidencia: [cita textual del codigo]
  Impacto: [carpeta borrada con archivos, ruta rota -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. El saneamiento es seguro."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
