---
name: audit-data-contracts
description: Verifica la consistencia entre FacturaRecord, SelectionVM, MainWindowState y los dataframes de reportes. Detecta campos ambiguos, nullabilidad inconsistente y derivados duplicados. Usar esta skill cuando mencionas modelo de datos, contrato, FacturaRecord, SelectionVM, view model, campos inconsistentes, None inesperado, monto como string, estado ambiguo, tipo de dato incorrecto, nullabilidad, contrato de exportacion.
---

# Auditoria: Contratos de Datos y View Models

Sos un auditor especializado en la consistencia de los modelos de datos entre capas. Un campo que es `str` en `FacturaRecord` pero `Decimal` en el dataframe de exportacion es un bug latente.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas inconsistencias. Lee los modelos reales y compara campo por campo. Si los tipos son consistentes, reporta "SIN PROBLEMAS".

## Alcance y limites

Este skill audita SOLO la consistencia de los modelos de datos:
- `gestor_contable/core/models.py` -- `FacturaRecord`: fuente de verdad del dominio
- `gestor_contable/app/selection_vm.py` -- `SelectionVM`: contrato del panel de clasificacion
- `gestor_contable/app/state/main_window_state.py` -- estado de ventana
- `gestor_contable/app/selection_controller.py` -- como se construye SelectionVM desde FacturaRecord
- `gestor_contable/app/use_cases/export_report_use_case.py` -- contrato de exportacion

**Fuera de alcance** (lo cubren otros skills):
- Si gui/ accede directo a FacturaRecord --> audit-gui-layers
- Categorias y ruta destino --> audit-accounting-classify
- Tamano de archivos --> audit-hotspots

## Paso 1: Mapear campos del modelo principal

Leer `core/models.py` y listar todos los campos de `FacturaRecord`:
- Tipo declarado
- Valor por defecto
- Si puede ser None

## Paso 2: Verificar consistencia con SelectionVM

Leer `app/selection_vm.py` y `app/selection_controller.py`:
- Para cada campo de SelectionVM, verificar que el tipo coincida con FacturaRecord
- Buscar conversiones implicitas (str(), or '', or 0)

## Paso 3: Verificar contrato de exportacion

Leer `export_report_use_case.py`:
- Columnas del dataframe y sus tipos
- Conversiones de tipo (.astype, pd.to_numeric, float())
- Manejo de nulos (fillna, dropna)

## Paso 4: Verificar estados documentales

Los estados validos son: `pendiente`, `pendiente_pdf`, `sin_xml`, `clasificado`.
```
Buscar en gestor_contable/:
1. '== "clasificado"|== "pendiente"' -- comparaciones de estado (consistentes?)
2. "ESTADOS|estados.*=.*\[" -- constante centralizada?
```

## Paso 5: Generar reporte

```
AUDITORIA: CONTRATOS DE DATOS
================================

Archivos revisados: [lista de archivos que realmente leiste]

CAMPOS DE FacturaRecord
-------------------------
campo          | tipo      | nullable | default
[campo]        | [tipo]    | [SI/NO]  | [valor]
[...continuar para todos los campos]

CONSISTENCIA FacturaRecord <-> SelectionVM
--------------------------------------------
[Para cada campo del VM:]
campo_vm       | tipo_vm   | campo_record  | tipo_record | consistente
[campo]        | [tipo]    | [campo]       | [tipo]      | [SI/NO]

ESTADOS DOCUMENTALES
----------------------
Constante centralizada: [SI (donde) / NO (strings dispersos)]
Valores usados en comparaciones: [lista encontrada]

HALLAZGOS
---------
[Solo inconsistencias reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion de la inconsistencia
  Evidencia: [cita textual del codigo]
  Impacto: [crash, dato incorrecto en Excel -- cual exactamente]

[Si no hay inconsistencias: "Ningun hallazgo. Los contratos son consistentes."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
