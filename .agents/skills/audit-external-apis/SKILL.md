---
name: audit-external-apis
description: Audita las llamadas a APIs externas Hacienda, ATV y CABYS -- timeouts, retries, gestion de credenciales y acoplamiento con cache. Detecta retries mal calibrados, leaks de credenciales y manejo insuficiente de errores HTTP. Usar esta skill cuando mencionas API Hacienda, ATV, CABYS, credenciales, token, keyring, timeout, retry, 429, 5xx, integracion externa, autenticacion, error de red, API caida.
---

# Auditoria: Integraciones Externas y Credenciales

Sos un auditor especializado en la resiliencia y seguridad de las integraciones externas. El sistema consulta APIs de Hacienda, ATV y CABYS.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas que todas las llamadas HTTP carecen de timeout. Verifica cada una individualmente.

## Alcance y limites

Este skill audita SOLO las llamadas HTTP y gestion de credenciales:
- `gestor_contable/core/xml_manager.py` -- API Hacienda y cache
- `gestor_contable/core/atv_client.py` -- cliente ATV (autenticacion, estado de comprobantes)
- `gestor_contable/core/cabys_manager.py` -- fetch de catalogo CABYS

**Fuera de alcance** (lo cubren otros skills):
- Cache de hacienda_cache.db --> audit-cache
- Errores silenciosos en llamadas HTTP --> audit-silent-errors
- Configuracion de timeouts via get_setting() --> audit-config-paths

## Paso 1: Mapear todas las llamadas HTTP

```
Buscar en gestor_contable/:
1. "requests\.(get|post|put|patch|delete)\(" -- toda llamada HTTP
2. "timeout=" -- tiene timeout configurado?
3. "retry|max_retries|for.*range.*retry" -- logica de reintentos
```

## Paso 2: Verificar manejo de errores HTTP

Para cada llamada HTTP encontrada:
```
1. "status_code.*429|too_many_requests" -- manejo de rate limit
2. "status_code.*5\d\d|500|503" -- errores de servidor
3. "except.*requests\.|except.*ConnectionError|except.*Timeout" -- excepciones de red
4. "backoff|sleep.*retry|exponential" -- backoff entre reintentos
```

## Paso 3: Verificar credenciales

```
Buscar en atv_client.py:
1. "keyring\.(get|set)_password" -- uso de keyring
2. "password|token|api_key|secret" en strings literales -- hardcodeadas?
3. "logger.*token|logger.*password|print.*token" -- logging de credenciales (PROHIBIDO)
4. "Bearer|Authorization" -- headers de autenticacion
```

## Paso 4: Generar reporte

```
AUDITORIA: INTEGRACIONES EXTERNAS
====================================

Archivos revisados: [lista de archivos que realmente leiste]

INVENTARIO DE LLAMADAS HTTP
-----------------------------
API       | Modulo         | Timeout | Retry | Backoff | Errores HTTP manejados
Hacienda  | xml_manager.py | [?]     | [?]   | [?]     | [?]
ATV       | atv_client.py  | [?]     | [?]   | [?]     | [?]
CABYS     | cabys_manager  | [?]     | [?]   | [?]     | [?]

CREDENCIALES
--------------
Almacenamiento: [keyring / hardcoded / otro]
Logging de secretos: [NO (correcto) / SI (violacion) -- linea:N]

HALLAZGOS
---------
[Solo problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema
  Evidencia: [cita textual del codigo]
  Impacto: [timeout infinito, leak de credenciales -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. Las integraciones son resilientes."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
