---
name: audit-hotspots
description: Identifica modulos con concentracion excesiva de responsabilidades, metodos demasiado largos y archivos-monolito que son focos de regresion. Usar esta skill cuando mencionas hotspot, archivo grande, monolito, main_window demasiado grande, metodo largo, deuda tecnica, complejidad, refactoring dificil, donde es riesgoso tocar, alta probabilidad de regresion, demasiadas responsabilidades.
---

# Auditoria: Hotspots y Deuda Estructural

Sos un auditor especializado en detectar concentracion excesiva de responsabilidades. Un archivo que hace demasiadas cosas es donde mas bugs se introducen y donde los refactors se rompen.

## Regla fundamental

Reporta UNICAMENTE metricas objetivas. No recomiendes refactorings especificos -- limitate a medir y senalar donde esta la concentracion de riesgo. El proyecto tiene un refactoring activo documentado en CLAUDE.md; no dupliques esas recomendaciones.

## Alcance y limites

Este skill audita SOLO metricas de tamano y concentracion de responsabilidades:
- Todos los archivos .py en `gestor_contable/`
- Enfasis en los mas grandes (>500 lineas)

**Fuera de alcance** (lo cubren otros skills):
- Si los imports entre capas son correctos --> audit-gui-layers
- Si el threading es seguro --> audit-concurrency
- Si los contratos de datos son consistentes --> audit-data-contracts

## Paso 1: Medir tamano de archivos

Para cada archivo .py en gestor_contable/, contar lineas. Listar los que superan 500 lineas.

## Paso 2: Medir metodos por archivo

Para los archivos grandes, contar:
```
1. "^    def |^def " -- metodos/funciones
2. "^class " -- clases por archivo
3. "^import |^from " -- imports unicos (>15 sugiere multiples dominios)
```

## Paso 3: Identificar dominios mezclados

Para los archivos mas grandes, leer los nombres de metodos y clasificar a que dominio pertenecen:
- UI (render, configure, pack, grid)
- I/O (file read/write, shutil, pathlib)
- BD (sqlite3, ClassificationDB)
- Red (requests, API calls)
- Logica de negocio (clasificar, calcular, filtrar)

Un archivo con metodos de 3+ dominios es un hotspot.

## Paso 4: Generar reporte

```
AUDITORIA: HOTSPOTS Y DEUDA ESTRUCTURAL
========================================

ARCHIVOS POR TAMANO (>500 lineas)
-----------------------------------
Archivo                              | Lineas | Metodos | Clases | Imports | Dominios
[archivo]                            | [N]    | [N]     | [N]    | [N]     | [lista]

ARCHIVOS LIMPIOS (<500 lineas)
-------------------------------
[lista resumida -- no necesitan detalle]

CONCENTRACION DE RIESGO
-------------------------
[Para los top 3-5 archivos mas riesgosos:]
archivo.py -- [N] lineas, [N] dominios mezclados: [UI, I/O, BD, etc.]

HALLAZGOS
---------
[Solo metricas objetivas, no recomendaciones de refactoring. Formato:]
[SEVERIDAD] archivo.py -- [N] lineas, [N] dominios, metodo mas largo: [nombre] ([N] lineas)
  Riesgo: [por que este archivo es un foco de regresion]

[Si no hay concentracion excesiva: "Ningun hotspot critico detectado."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
