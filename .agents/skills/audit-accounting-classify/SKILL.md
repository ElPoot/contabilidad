---
name: audit-accounting-classify
description: Audita como se decide la categoria contable (COMPRAS/GASTOS/ACTIVO/OGND), la cuenta del catalogo, y como se construye la ruta de destino del comprobante clasificado. Detecta rutas invalidas, categorias inconsistentes y cuentas mal aplicadas. Usar esta skill cuando mencionas clasificacion contable, COMPRAS, GASTOS, ACTIVO, OGND, categoria, subcategoria, catalogo de cuentas, ruta destino, destino incorrecto, cuenta contable, reclasificar.
---

# Auditoria: Clasificacion Contable y Construccion de Destinos

Sos un auditor especializado en la integridad de las reglas de clasificacion contable. El sistema clasifica comprobantes en COMPRAS, GASTOS, ACTIVO, OGND. La ruta de destino se construye combinando mes, cliente, categoria, subcategoria y emisor sanitizado.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas que las categorias estan mal definidas. Verifica la consistencia real entre los modulos.

## Alcance y limites

Este skill audita SOLO las categorias contables y la construccion de ruta destino:
- `gestor_contable/core/classifier.py` -- `classify_record()`, `build_dest_folder()`, `_sanitize_folder()`
- `gestor_contable/core/catalog.py` -- `CatalogManager`: catalogo de cuentas por cliente
- `gestor_contable/core/classification_utils.py` -- filtros y estadisticas por categoria
- `gestor_contable/gui/classify_panel.py` -- opciones presentadas al usuario

**Fuera de alcance** (lo cubren otros skills):
- Protocolo SHA256 del movimiento de archivos --> audit-safe-move
- Saneamiento de carpetas vacias --> audit-path-sanitize
- Contratos de datos (FacturaRecord, SelectionVM) --> audit-data-contracts

## Paso 1: Verificar consistencia de categorias

```
Buscar en gestor_contable/:
1. '"COMPRAS"|"GASTOS"|"ACTIVO"|"OGND"' -- las 4 categorias
2. '"compras"|"gastos"|"activo"|"ognd"' -- variantes en minusculas (inconsistencia?)
3. "CATEGORIAS|VALID_CATS|allowed" -- constante centralizada?
```

## Paso 2: Verificar construccion de ruta destino

```
Buscar en classifier.py:
1. "build_dest_folder|dest_folder|destino" -- funcion de construccion
2. "Contabilidades" -- raiz del directorio de clasificados
3. "_sanitize_folder\(" -- sanitiza nombre del emisor?
4. "mkdir.*parents|exist_ok" -- creacion del directorio
```

## Paso 3: Verificar catalogo de cuentas

```
Buscar en catalog.py:
1. "CatalogManager|load_catalog" -- carga del catalogo
2. "get_cuenta|find_cuenta|catalog\.get" -- consulta
3. "default.*''|sin_cuenta" -- que pasa si la cuenta no esta en el catalogo
```

## Paso 4: Generar reporte

```
AUDITORIA: CLASIFICACION CONTABLE
====================================

Archivos revisados: [lista de archivos que realmente leiste]

CATEGORIAS
-----------
Categorias definidas: [lista encontrada]
Centralizada en constante: [SI (donde) / NO (dispersa en N archivos)]
Consistencia mayusculas/minusculas: [SI / NO -- detalle]

RUTA DE DESTINO
----------------
Patron: [Contabilidades/{mes}/{cliente}/{categoria}/...? documentar el patron real]
_sanitize_folder en emisor: [SI linea:N / NO]
mkdir con exist_ok: [SI / NO]

CATALOGO DE CUENTAS
---------------------
Cuenta faltante: [retorna '' / raise / otro]

HALLAZGOS
---------
[Solo problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema
  Evidencia: [cita textual del codigo]
  Impacto: [ruta invalida, categoria inconsistente -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. La clasificacion es consistente."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
