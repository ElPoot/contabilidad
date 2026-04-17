---
name: audit-release
description: Audita el build, versionado, changelog, PyInstaller y dependencia de entorno Windows. Detecta releases no reproducibles, hidden imports faltantes y artefactos inconsistentes. Usar esta skill cuando mencionas build, release, PyInstaller, version, changelog, empaquetado, exe, dist, spec, hidden imports, reproducible.
---

# Auditoria: Release Engineering y Empaquetado

Sos un auditor especializado en el pipeline de build y release. El sistema se empaqueta con PyInstaller para distribuir un ejecutable Windows.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas que el build esta roto. Verifica la configuracion real.

## Alcance y limites

Este skill audita SOLO el release engineering:
- `build.py` -- script de build
- `gestor_contable.spec` -- configuracion PyInstaller
- `gestor_contable/version.py` -- versionado
- `requirements.txt` -- dependencias

**Fuera de alcance** (lo cubren otros skills):
- Artefactos versionados en el repo --> audit-versioned-artifacts
- Configuracion y rutas --> audit-config-paths

## Paso 1: Verificar reproducibilidad del build

```
Buscar en build.py y gestor_contable.spec:
1. "hidden.*import|hiddenimports" -- imports ocultos para PyInstaller
2. "datas|binaries|collect" -- archivos incluidos en el bundle
3. "excludes|exclude" -- exclusiones explicitas
4. "onefile|onedir" -- modo de empaquetado
```

## Paso 2: Verificar versionado

```
Buscar en gestor_contable/:
1. "version|__version__|VERSION" -- definicion de version
2. "tag|commit.*version" -- automatizacion de tags
3. "changelog|CHANGELOG" -- historial de cambios
```

## Paso 3: Verificar dependencias empaquetadas

```
Comparar:
1. requirements.txt -- dependencias declaradas
2. hiddenimports en .spec -- imports ocultos declarados
3. "import " en gestor_contable/**/*.py -- imports reales usados
```

## Paso 4: Generar reporte

```
AUDITORIA: RELEASE ENGINEERING Y EMPAQUETADO
================================================

Archivos revisados: [lista de archivos que realmente leiste]

REPRODUCIBILIDAD
------------------
Modo: [onefile / onedir]
Hidden imports: [lista]
Datas incluidos: [lista]
Exclusiones: [lista]

VERSIONADO
-----------
Fuente de version: [version.py / otro]
Tags automaticos: [SI / NO]
Changelog: [existe / NO]

DEPENDENCIAS
--------------
En requirements.txt: [N paquetes]
Hidden imports vs imports reales: [consistente / faltantes: lista]

HALLAZGOS
---------
[Solo problemas reales. Formato:]
[SEVERIDAD] archivo:linea N -- descripcion del problema
  Evidencia: [cita textual]
  Impacto: [build falla, import faltante -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. El pipeline de release es consistente."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
