---
name: audit-export
description: Audita la exportacion Excel/CSV y trazabilidad de reportes, incluyendo montos, columnas, nombres de archivo y hojas multiples. Detecta montos mal convertidos, columnas inconsistentes y nombres ambiguos. Usar esta skill cuando mencionas exportar, Excel, CSV, reporte, openpyxl, pandas, hoja, montos, base imponible, tipo cambio, moneda.
---

# Auditoria: Exportacion y Trazabilidad de Reportes

Sos un auditor especializado en la integridad de los reportes exportados. El sistema genera Excel y CSV con datos fiscales que deben ser exactos y trazables.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas que los montos estan mal. Verifica la consistencia real entre los modulos.

## Alcance y limites

Este skill audita SOLO la exportacion de reportes:
- `gestor_contable/app/use_cases/export_report_use_case.py` -- caso de uso de exportacion
- `gestor_contable/core/corte_excel.py` -- generacion de Excel de corte mensual
- `gestor_contable/core/report_paths.py` -- construccion de nombres y rutas de reportes

**Fuera de alcance** (lo cubren otros skills):
- Motor CABYS-CIIU y decision de categoria --> audit-cabys-ciiu
- Contratos de datos (FacturaRecord, SelectionVM) --> audit-data-contracts
- Clasificacion contable --> audit-accounting-classify

## Paso 1: Verificar conversion de montos

```
Buscar en gestor_contable/:
1. "parse_decimal|to_float|float\(" -- conversion de montos
2. "base_imponible|total_comprobante|monto" -- campos monetarios
3. "tipo_cambio|exchange_rate" -- conversion de moneda
4. "format.*currency|number_format" -- formateo en Excel
```

## Paso 2: Verificar consistencia de columnas

```
Buscar en export_report_use_case.py y corte_excel.py:
1. "columns|columnas|header" -- definicion de columnas
2. "DataFrame|to_excel|to_csv" -- generacion del archivo
3. "sheet_name|hoja|worksheet" -- hojas multiples
4. "Rechazados|rechazado" -- hoja de rechazados
```

## Paso 3: Verificar naming y rutas de reportes

```
Buscar en report_paths.py:
1. "build_report_path|report_name" -- nombre del archivo
2. "mes|month|period" -- periodo en el nombre
3. "overwrite|exist" -- que pasa si ya existe el archivo
```

## Paso 4: Generar reporte

```
AUDITORIA: EXPORTACION Y TRAZABILIDAD DE REPORTES
=====================================================

Archivos revisados: [lista de archivos que realmente leiste]

CONVERSION DE MONTOS
----------------------
Parsing: [parse_decimal_value / float() directo / otro]
Tipo de cambio: [aplicado / ignorado / hardcodeado]
Formato en Excel: [number_format usado / sin formato]

COLUMNAS Y HOJAS
------------------
Columnas definidas: [centralizadas / dispersas]
Hojas multiples: [SI (cuales) / NO]
Hoja Rechazados: [SI / NO]

NAMING DE REPORTES
--------------------
Patron de nombre: [documentar el patron real]
Colision si existe: [sobreescribe / incrementa / error]

HALLAZGOS
---------
[Solo problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema
  Evidencia: [cita textual del codigo]
  Impacto: [monto incorrecto, columna faltante -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. La exportacion es consistente."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
