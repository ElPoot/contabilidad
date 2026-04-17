---
name: audit-cabys-ciiu
description: Audita el motor CABYS-CIIU, reglas sectoriales, clasificacion automatica y persistencia de decisiones del contador en cortes mensuales. Detecta afinidades incorrectas, sesgo por capitulos, vendor catalog contaminado. Usar esta skill cuando mencionas CABYS, CIIU, corte mensual, afinidad sectorial, capitulo, vendor catalog, ambiguo, bien_es_compra, compras_capitulos.
---

# Auditoria: Motor CABYS-CIIU y Cortes Mensuales

Sos un auditor especializado en las reglas sectoriales CABYS-CIIU y el motor de corte mensual. El sistema usa afinidades por actividad economica (CIIU) y codigo de producto (CABYS) para decidir automaticamente si un bien es COMPRAS o GASTOS, y escala a decision manual cuando hay ambiguedad.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas que las afinidades estan mal. Verifica la consistencia real entre los modulos.

## Alcance y limites

Este skill audita SOLO el motor CABYS-CIIU y cortes mensuales:
- `gestor_contable/core/cabys_manager.py` -- CabysManager: lazy fetch, capitulos, afinidad
- `gestor_contable/core/corte_engine.py` -- motor de corte mensual
- `gestor_contable/core/ciiu_affinity.json` -- reglas sectoriales por CIIU
- `gestor_contable/gui/corte_ambiguo_modal.py` -- modal de decision para ambiguos

**Fuera de alcance** (lo cubren otros skills):
- Clasificacion contable final (COMPRAS/GASTOS/ACTIVO/OGND) --> audit-accounting-classify
- Exportacion de reportes de corte --> audit-export
- Contratos de datos --> audit-data-contracts

## Paso 1: Verificar motor de afinidad

```
Buscar en gestor_contable/:
1. "bien_es_compra|is_purchase" -- funcion principal de decision
2. "compras_capitulos|purchase_chapters" -- capitulos que fuerzan COMPRAS
3. "ciiu_affinity|affinity" -- carga de reglas sectoriales
4. "ambig|uncertain|manual" -- escalamiento a decision del contador
```

## Paso 2: Verificar vendor catalog y persistencia

```
Buscar en gestor_contable/:
1. "vendor_catalog|VendorCatalog" -- catalogo por proveedor/cliente
2. "save_decision|persist|guardar" -- persistencia de decisiones manuales
3. "capitulos_extra|extra_chapters" -- capitulos adicionales por cliente
4. "contamina|override|sobreescri" -- riesgo de contaminacion del catalog
```

## Paso 3: Verificar corte mensual

```
Buscar en corte_engine.py:
1. "corte|monthly_cut|period" -- flujo principal
2. "pendiente|sin_decidir" -- items que no se resolvieron
3. "batch|lote" -- procesamiento en lote
4. "rollback|deshacer" -- reversibilidad de decisiones
```

## Paso 4: Generar reporte

```
AUDITORIA: MOTOR CABYS-CIIU Y CORTES MENSUALES
=================================================

Archivos revisados: [lista de archivos que realmente leiste]

AFINIDAD SECTORIAL
--------------------
Fuente de reglas: [ciiu_affinity.json / hardcodeado / otro]
Capitulos COMPRAS: [lista encontrada]
Logica de ambiguedad: [describe el criterio real]
Escalamiento manual: [SI (donde) / NO]

VENDOR CATALOG
---------------
Persistencia: [JSON / SQLite / otro]
Contaminacion cruzada: [riesgo detectado / NO]
Capitulos extra por cliente: [SI (mecanismo) / NO]

CORTE MENSUAL
--------------
Items pendientes sin decidir: [como se manejan]
Reversibilidad: [SI / NO]

HALLAZGOS
---------
[Solo problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema
  Evidencia: [cita textual del codigo]
  Impacto: [afinidad incorrecta, decision perdida -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. El motor CABYS-CIIU es consistente."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
