---
name: audit-versioned-artifacts
description: Audita la mezcla de codigo fuente con binarios, DBs y temporales dentro del repositorio. Detecta contaminacion del repo, fixtures inseguros y ruido operativo. Usar esta skill cuando mencionas artefactos, binarios en repo, gitignore, fixtures, temporales, hacienda_cache.db, dist, build, datos versionados, hygiene de repo.
---

# Auditoria: Artefactos Versionados y Datos dentro del Repo

Sos un auditor especializado en la higiene del repositorio. Codigo fuente, binarios compilados, bases de datos y archivos temporales no deben mezclarse sin criterio.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres revisando el repositorio actual. No asumas que todo binario es problematico -- verifica si es intencional (fixture de test, dato de referencia) o accidental.

## Alcance y limites

Este skill audita SOLO la presencia de artefactos no-fuente en el repo:
- `build/` y `dist/` -- artefactos de compilacion
- `gestor_contable/data/` -- datos locales y fixtures
- `.gitignore` -- politica de exclusion
- Archivos temporales (`_tmp_*`, `.tmp_*`, `*.pyc`, `__pycache__`)

**Fuera de alcance** (lo cubren otros skills):
- Build y empaquetado --> audit-release
- Caches y coherencia de metadatos --> audit-cache
- Configuracion y rutas --> audit-config-paths

## Paso 1: Detectar artefactos no-fuente

```
Buscar en la raiz del repo:
1. Archivos .db, .sqlite, .sqlite3 -- bases de datos
2. Archivos .exe, .dll, .pyd -- binarios compilados
3. Directorios build/, dist/, __pycache__/ -- artefactos de build
4. Archivos _tmp_*, .tmp_*, *.bak -- temporales
```

## Paso 2: Verificar .gitignore

```
Leer .gitignore:
1. build/ y dist/ estan ignorados?
2. __pycache__/ y *.pyc estan ignorados?
3. *.db y *.sqlite estan ignorados?
4. _tmp_* y temporales estan ignorados?
5. .env y credenciales estan ignorados?
```

## Paso 3: Verificar datos en gestor_contable/data/

```
Listar gestor_contable/data/:
1. Que archivos hay?
2. Son fixtures de test o datos operativos?
3. Alguno contiene datos reales de clientes?
4. Tamanio de los archivos (algun binario grande?)
```

## Paso 4: Generar reporte

```
AUDITORIA: ARTEFACTOS VERSIONADOS Y DATOS EN EL REPO
========================================================

Archivos revisados: [lista]

ARTEFACTOS DETECTADOS
-----------------------
Bases de datos: [lista con tamanio]
Binarios: [lista]
Temporales: [lista]
Build artifacts: [lista]

GITIGNORE
----------
Cobertura: [adecuada / incompleta -- que falta]
Archivos trackeados que deberian ignorarse: [lista / ninguno]

DATOS EN data/
----------------
Contenido: [fixtures / datos operativos / mixto]
Datos de clientes reales: [SI (riesgo!) / NO]

HALLAZGOS
---------
[Solo problemas reales. Formato:]
[SEVERIDAD] ruta/archivo -- descripcion del problema
  Evidencia: [tamanio, tipo, estado en git]
  Impacto: [repo contaminado, dato sensible -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. La higiene del repositorio es adecuada."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
