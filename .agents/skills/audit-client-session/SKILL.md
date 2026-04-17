---
name: audit-client-session
description: Audita la resolucion de carpeta por cedula y nombre de cliente, la curacion automatica con Hacienda y la coherencia de perfiles. Detecta sesion del cliente equivocado, renombres peligrosos y perfiles incoherentes. Usar esta skill cuando mencionas sesion cliente, cedula, resolve_client_session, client_profiles, _heal_client, login, perfil incorrecto, cliente equivocado, onboarding, nombre cliente no coincide, CIIU incorrecto.
---

# Auditoria: Sesion de Cliente y Autocuracion de Identidad

Sos un auditor especializado en la integridad de la identidad de clientes. El sistema resuelve la carpeta de trabajo por cedula fiscal. Si el sistema abre la carpeta equivocada, muestra comprobantes de otro cliente.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas que la resolucion de sesion es incorrecta. Verifica el flujo completo: entrada de cedula, busqueda en perfiles, apertura de carpeta.

## Alcance y limites

Este skill audita SOLO la resolucion de sesion y curacion de identidad:
- `gestor_contable/core/session.py` -- `ClientSession`, `resolve_client_session()`
- `gestor_contable/gui/session_view.py` -- UI de login, `_heal_client()`
- `gestor_contable/core/client_profiles.py` -- `load_profiles()`, estructura de perfiles

**Fuera de alcance** (lo cubren otros skills):
- Saneamiento de rutas y carpetas --> audit-path-sanitize
- Rutas hardcodeadas y portabilidad --> audit-config-paths
- API de Hacienda (timeout, retry) --> audit-external-apis

## Paso 1: Verificar resolucion por cedula

```
Buscar en session.py y client_profiles.py:
1. "cedula|identificacion" -- campo de cedula
2. "strip\(\)|replace\('-'|replace\(' ')" -- normalizacion
3. "==.*cedula|cedula.*==" -- comparacion (exacta o parcial?)
```

## Paso 2: Verificar _heal_client

```
Buscar en session_view.py:
1. "_heal_client|heal.*client" -- implementacion
2. "rename|Path.*rename" -- renombrado de carpeta
3. "confirm|messagebox|dialog" -- pide confirmacion al usuario?
4. "profiles.*update|save.*profiles" -- actualiza perfil tras rename?
```

## Paso 3: Verificar consistencia de perfiles

```
Buscar en client_profiles.py:
1. "load_profiles|json\.load" -- carga de perfiles
2. "except.*KeyError|except.*json" -- manejo de perfil malformado
3. "ciiu|actividad" -- campo CIIU por cliente
```

## Paso 4: Generar reporte

```
AUDITORIA: SESION DE CLIENTE
==============================

Archivos revisados: [lista de archivos que realmente leiste]

FLUJO DE RESOLUCION
---------------------
Entrada: [cedula / nombre / ambos]
Normalizacion: [SI (que hace) / NO]
Busqueda: [exacta / parcial / fuzzy]
Verificacion de carpeta: [SI / NO]

HEAL_CLIENT
------------
Pide confirmacion al usuario: [SI / NO]
Actualiza perfiles tras rename: [SI / NO]
Actualiza rutas en SQLite: [SI / NO]

HALLAZGOS
---------
[Solo problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema
  Evidencia: [cita textual del codigo]
  Impacto: [cliente equivocado, rename sin confirmar -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. La resolucion de sesion es correcta."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
