---
name: audit-config-paths
description: "Revisa la dependencia de Z:/DATA, el montaje via subst, los placeholders de OneDrive y la portabilidad del sistema en entornos Windows. Detecta boot fragil, rutas hardcodeadas y fallos cuando OneDrive no esta descargado. Usar esta skill cuando mencionas configuracion rutas, Z: no disponible, OneDrive placeholder, subst, network_drive, setup_window, portabilidad Windows, rutas hardcodeadas, offline."
---

# Auditoria: Configuracion, Rutas y Portabilidad Windows/OneDrive

Sos un auditor especializado en la resiliencia del sistema en distintos entornos Windows. La app depende de una unidad logica `Z:` montada sobre OneDrive.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas que todas las rutas estan hardcodeadas. Verifica primero si pasan por `network_drive()` o `get_setting()`.

## Alcance y limites

Este skill audita SOLO rutas, configuracion y portabilidad:
- `gestor_contable/config.py` -- `network_drive()`, `client_root()`, `ensure_drive_mounted()`
- `gestor_contable/core/settings.py` -- `get_setting()`, lectura de settings.json
- `gestor_contable/gui/setup_window.py` -- UI de configuracion fallback

**Fuera de alcance** (lo cubren otros skills):
- Sesion de cliente y perfiles --> audit-client-session
- Visor PDF y placeholders --> audit-pdf-viewer
- APIs externas --> audit-external-apis

## Paso 1: Buscar rutas hardcodeadas

```
Buscar en gestor_contable/ (excluyendo config.py):
1. "Z:/|Z:\\\\" -- letra Z: hardcodeada fuera de config.py
2. "C:/Users|C:\\\\Users" -- rutas hardcodeadas a C:
```

## Paso 2: Verificar flujo de arranque sin Z:

Leer `config.py` y `main.py`:
```
1. "ensure_drive_mounted|subst" -- montaje de unidad
2. "exists\(\).*Z|Path.*Z.*exists" -- verificacion de disponibilidad
3. "setup_window|SetupWindow" -- fallback cuando Z: falla
4. "sys\.exit|raise.*SystemExit" -- que hace si no hay ruta
```

## Paso 3: Verificar get_setting

```
Buscar en settings.py:
1. "get_setting" -- implementacion
2. "default\s*=|DEFAULT" -- valores por defecto
3. "except.*FileNotFoundError|except.*json" -- settings.json ausente o corrupto
```

## Paso 4: Generar reporte

```
AUDITORIA: CONFIGURACION Y PORTABILIDAD
=========================================

Archivos revisados: [lista de archivos que realmente leiste]

RUTAS HARDCODEADAS
-------------------
[lista de archivos con rutas hardcodeadas fuera de config.py, o "Ninguna encontrada"]

FLUJO DE ARRANQUE
------------------
Z: disponible: [que hace -- linea:N]
Z: no disponible: [que hace -- linea:N]
settings.json corrupto/ausente: [que hace -- linea:N]

HALLAZGOS
---------
[Solo problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema
  Evidencia: [cita textual del codigo]
  Impacto: [app no arranca, ruta incorrecta -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. La configuracion es portable."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
