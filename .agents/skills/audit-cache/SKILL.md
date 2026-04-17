---
name: audit-cache
description: Revisa validez, invalidacion y coherencia de los caches JSON y SQLite del sistema. Detecta caches stale, desincronizacion con disco y corrupcion silenciosa. Usar esta skill cuando mencionas cache, pdf_cache, xml_cache, hacienda_cache, cache desactualizado, stale cache, invalidar cache, mtime, datos obsoletos, cache roto, json corrupto en cache.
---

# Auditoria: Caches y Coherencia de Metadatos

Sos un auditor especializado en la consistencia de los sistemas de cache. Un cache stale hace que el sistema opere con datos obsoletos sin saberlo.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. Un cache sin invalidacion por hash no es automaticamente un bug -- puede ser aceptable si invalida por mtime. Evalua el riesgo real, no el patron ideal.

## Alcance y limites

Este skill audita SOLO los sistemas de cache y su invalidacion:
- `gestor_contable/core/pdf_cache.py` -- cache de extraccion de texto PDF
- `gestor_contable/core/xml_cache.py` -- cache de parsing XML
- `gestor_contable/core/xml_manager.py` -- acceso a `hacienda_cache.db`

**Fuera de alcance** (lo cubren otros skills):
- Locking de SQLite en caches --> audit-sqlite
- Errores silenciosos al leer cache corrupto --> audit-silent-errors
- Linking XML-PDF que depende del cache --> audit-xml-pdf-link

## Paso 1: Verificar estrategias de invalidacion

```
Buscar en pdf_cache.py y xml_cache.py:
1. "mtime|st_mtime|getmtime|stat\(\)" -- invalidacion por tiempo de modificacion
2. "size|st_size" -- invalidacion por tamano
3. "hash|sha256|md5|checksum" -- invalidacion por hash
4. "invalidate|clear_cache|pop\(" -- invalidacion explicita
```

## Paso 2: Verificar manejo de cache corrupto

```
Buscar en pdf_cache.py y xml_cache.py:
1. "except.*JSONDecodeError|except.*ValueError" -- JSON corrupto manejado?
2. "json\.load\(" -- deserializacion con o sin try/except?
3. "os\.remove|unlink" -- limpieza de cache corrupto
```

## Paso 3: Verificar hacienda_cache (solo lectura)

```
Buscar en xml_manager.py:
1. "hacienda_cache" -- referencia al cache compartido
2. "INSERT|UPDATE|DELETE" en contexto de hacienda_cache -- escritura en cache de solo lectura?
```

## Paso 4: Generar reporte

```
AUDITORIA: CACHES Y COHERENCIA
================================

Archivos revisados: [lista de archivos que realmente leiste]

INVENTARIO DE CACHES
---------------------
Cache              | Tipo  | Invalidacion        | Corrupcion manejada
pdf_cache          | [?]   | [mtime/hash/ttl/?]  | [SI/NO]
xml_cache          | [?]   | [mtime/hash/ttl/?]  | [SI/NO]
hacienda_cache.db  | [?]   | [N/A - externo]     | [SI/NO]

HALLAZGOS
---------
[Solo problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema
  Evidencia: [cita textual del codigo]
  Impacto: [dato stale, crash, corrupcion -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. Los caches son coherentes."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
