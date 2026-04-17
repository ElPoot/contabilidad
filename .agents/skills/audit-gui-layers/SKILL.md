---
name: audit-gui-layers
description: Detecta business logic infiltrada en gui/, acoplamiento incorrecto entre capas y operaciones de I/O o persistencia que no deben estar en vistas. Usar esta skill cuando mencionas capas GUI, acoplamiento, logica en vistas, imports entre capas, gui importa core directamente, main_window con demasiada responsabilidad, refactor de capas, separacion GUI-app-core, violacion de arquitectura, negocio en la vista.
---

# Auditoria: Limites GUI-App-Core

Sos un auditor especializado en verificar que la arquitectura de tres capas se respete. La regla es: `gui/` solo renderiza y delega, `app/` orquesta, `core/` concentra reglas de dominio.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. El proyecto esta en refactoring activo (ver CLAUDE.md) -- algunas violaciones de capas son conocidas y estan siendo migradas incrementalmente. No reportes como hallazgo algo que ya esta documentado como pendiente de migracion. Distingue entre violaciones nuevas y deuda tecnica conocida.

## Alcance y limites

Este skill audita SOLO los imports y dependencias entre capas:
- `gestor_contable/gui/` -- verificar que no contenga logica de dominio ni acceso directo a BD
- `gestor_contable/app/` -- verificar que no importe customtkinter
- `gestor_contable/core/` -- verificar que no importe desde gui/

**Fuera de alcance** (lo cubren otros skills):
- Patrones de threading en GUI --> audit-concurrency
- Tamano de archivos y metodos largos --> audit-hotspots
- Locking de SQLite --> audit-sqlite
- Contratos de datos entre capas --> audit-data-contracts

## Paso 1: Buscar imports prohibidos entre capas

```
1. "from gestor_contable\.gui" en core/ y app/ -- GUI importada en dominio (PROHIBIDO)
2. "import customtkinter|from customtkinter" en core/ y app/ -- UI framework en capas de negocio
3. "from gestor_contable\.core" en gui/ -- listar (no siempre es violacion, pero documentar)
```

## Paso 2: Buscar logica de dominio en gui/

```
Buscar en gui/:
1. "sqlite3\.connect|ClassificationDB" -- acceso directo a BD desde vista
2. "shutil\.|classify_record|safe_move_file" -- operaciones de archivo desde vista
3. "ciiu|cabys|catalogo|cuenta_contable" -- logica contable en vista
```

Para cada hallazgo, verificar si ya pasa por un controlador de `app/` o si es acceso directo.

## Paso 3: Verificar delegacion en app/

Leer los archivos en `gestor_contable/app/` y verificar:
- Que los controllers/use_cases importan de core/, no de gui/
- Que no contienen imports de customtkinter

## Paso 4: Generar reporte

```
AUDITORIA: LIMITES GUI-APP-CORE
================================

Archivos revisados: [lista de archivos que realmente leiste]

IMPORTS ENTRE CAPAS
--------------------
core/ importa de gui/: [NINGUNO (correcto) / lista de violaciones]
app/ importa customtkinter: [NINGUNO (correcto) / lista de violaciones]
gui/ importa de core/: [lista -- evaluar si pasan por app/ o son directos]

LOGICA DE DOMINIO EN GUI
--------------------------
[Para cada hallazgo encontrado:]
archivo.py:linea N -- [descripcion de lo que hace]
  Pasa por app/: [SI / NO (violacion)]

HALLAZGOS
---------
[Solo violaciones reales, no deuda tecnica ya documentada en CLAUDE.md. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion de la violacion
  Evidencia: [cita textual del import o llamada]
  Impacto: [que acoplamiento crea]

[Si no hay violaciones nuevas: "Ningun hallazgo nuevo. Las violaciones existentes estan documentadas en el refactoring activo."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
