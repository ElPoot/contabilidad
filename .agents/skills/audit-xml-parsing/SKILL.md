---
name: audit-xml-parsing
description: Audita robustez del parser XML ante encoding corruption, malformed XML, y ParseError recovery. Usa esta skill cuando menciones XML parsing, encoding issues, ParseError, XML corruption, caracteres invalidos, Latin-1 fallback, UTF-8, FAPEMO XMLs, MensajeHacienda, o fallos de carga XML.
---

# Auditoria: Parsing XML y Recuperacion de Encoding

Sos un auditor especializado en detectar cuando el parser XML falla ante datos corruptos del emisor y cuando deberia tener fallbacks mas robustos.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas bugs. Si el parser ya tiene fallbacks correctos, reporta "SIN PROBLEMAS". No inventes hallazgos para llenar el reporte.

## Alcance y limites

Este skill audita SOLO el parsing de archivos XML y su estrategia de encoding:
- `gestor_contable/core/xml_manager.py` -- `flatten_xml_stream()`, `_safe_parse_xml_file()`
- `gestor_contable/core/factura_index.py` -- recopilacion de `parse_errors`

**Fuera de alcance** (lo cubren otros skills):
- Extraccion de claves fiscales desde XML --> audit-fiscal-keys
- Cache de resultados de parsing --> audit-cache
- Errores silenciosos genericos --> audit-silent-errors

## Paso 1: Buscar puntos de parsing XML

Ejecutar estos Grep patterns:

```
1. "ET\.parse|ET\.fromstring|etree.*parse" -- donde ocurren los parses
2. "except\s+(ET\.ParseError|ParseError|XMLSyntaxError)" -- que hace cuando falla
3. "encoding.*utf-8|latin-1|iso-8859|cp1252" -- estrategia de fallback de encoding
4. "\.decode\(|\.encode\(" -- conversiones de bytes
5. "BOM|bom|UTF-8-sig|codecs" -- deteccion/manejo de BOM
```

## Paso 2: Leer y evaluar la estrategia de fallback

Leer `_safe_parse_xml_file()` y `flatten_xml_stream()` completos. Verificar:

1. Que encoding intenta primero y en que orden hace fallback
2. Si maneja el caso de XML que DECLARA UTF-8 pero contiene bytes Latin-1 (caso real: emisor FAPEMO)
3. Si ParseError se captura con contexto suficiente (archivo, linea, columna)
4. Si los errores de parsing se registran en algun lugar accesible (log, lista de errores)

## Paso 3: Verificar integracion con factura_index

Leer en `factura_index.py` como se manejan los parse_errors:
- Que pasa con un XML que falla el parsing -- se omite? se reintenta? se registra?
- El usuario puede ver cuales XMLs fallaron?

## Paso 4: Generar reporte

```
AUDITORIA: PARSING XML Y ENCODING
==================================

Archivos revisados: [lista de archivos que realmente leiste]

ESTRATEGIA DE ENCODING ACTUAL
-------------------------------
Funcion principal: [nombre y linea donde esta]
Orden de intentos: [UTF-8 -> Latin-1 -> ...? o solo UTF-8? describir lo que el codigo hace]
Caso FAPEMO (UTF-8 declarado, Latin-1 real): [MANEJADO linea:N / NO MANEJADO]
BOM handling: [SI / NO]

MANEJO DE ParseError
---------------------
Captura con contexto (archivo, linea): [SI linea:N / NO]
Logging del error: [SI (logger/print/lista) / NO (silenciado)]
Feedback al usuario: [SI / NO]

HALLAZGOS
---------
[Solo si encontraste problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema real encontrado
  Evidencia: [cita textual del codigo]
  Impacto: [consecuencia concreta]

[Si no hay problemas: "Ningun hallazgo. La estrategia de parsing es robusta."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
