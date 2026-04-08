# Repository Index — Gestor Contable

Este documento sirve como un mapa de alta densidad para agentes de IA (Gemini, Claude) y desarrolladores humanos para orientarse rápidamente en el repositorio del Gestor Contable.

## 🏗️ Arquitectura del Sistema

El proyecto sigue una arquitectura de capas (en transición hacia una separación de responsabilidades estricta):

- **`gestor_contable/gui/`**: Capa de Presentación (CustomTkinter). Contiene exclusivamente componentes de UI.
- **`gestor_contable/app/`**: Capa de Aplicación. Mediadora entre GUI y Core. Contiene Controladores, Casos de Uso y ViewModels (State).
- **`gestor_contable/core/`**: Capa de Dominio e Infraestructura. Lógica de negocio pura, parsing de XML, Base de Datos y protocolos de FileSystem. **Prohibido importar de GUI.**
- **`gestor_contable/config.py`**: Configuración global y gestión de rutas en la unidad de red (Z:/).

---

## 📂 Mapa de Directorios y Responsabilidades

### 1. Raíz del Proyecto
- `gestor_contable/main.py`: Punto de entrada. Inicializa la aplicación y la ventana principal.
- `gestor_contable/config.py`: Gestión centralizada de rutas (OneDrive/Unidad Z:).
- `requirements.txt`: Dependencias de Python.
- `GEMINI.md` / `CLAUDE.md`: Instrucciones de sistema para agentes de IA.
- `audit_prompt.md`: Guía para auditorías técnicas de seguridad e integridad.

### 2. Módulo Core (`gestor_contable/core/`)
- `xml_manager.py`: `CRXMLManager`. Carga masiva de XMLs, parsing y sincronización con API de Hacienda.
- `factura_index.py`: `FacturaIndexer`. Escaneo y vinculación de XMLs con PDFs mediante claves de 50 dígitos.
- `classifier.py`: `ClassificationDB`. Gestión de SQLite para categorización y protocolo atómico de movimiento (SHA256).
- `classification_utils.py`: Lógica de negocio para clasificación (Ingreso/Egreso/ORS) y filtrado por pestañas.
- `atv_client.py`: Cliente para la API ATV de Hacienda (consultas de estado y gestión de tokens).
- `models.py`: Estructuras de datos base (`FacturaRecord`).
- `iva_utils.py`: Lógica de cálculo de impuestos y manejo de decimales fiscales.
- `pdf_generator.py`: Generación de facturas PDF a partir de datos XML.
- `pdf_cache.py` / `xml_cache.py`: Persistencia de resultados de escaneo para acelerar la carga.
- `corte_engine.py` / `corte_excel.py`: Lógica de cierre mensual y generación de reportes en Excel.

### 3. Módulo de Aplicación (`gestor_contable/app/`)
- `use_cases/`: Operaciones de negocio complejas (ej. `export_report_use_case.py`).
- `controllers/`: Lógica de control para vistas específicas (ej. `selection_controller.py`).
- `state/`: ViewModels y estado de la UI (ej. `selection_vm.py`, `main_window_state.py`).
- `services/`: Servicios transversales de la aplicación.

### 4. Módulo GUI (`gestor_contable/gui/`)
- `main_window.py`: Layout principal de 3 columnas y orquestación de la UI.
- `pdf_viewer.py`: Visor de PDF personalizado basado en PyMuPDF (`fitz`).
- `session_view.py`: Login de cliente y selección de perfiles.
- `classify_panel.py`: Panel lateral para asignar categorías contables.
- `modal_overlay.py`: Clase base para diálogos modales personalizados.
- `icons.py`: Gestión de assets visuales.

---

## 🔑 Protocolos y Reglas Críticas

### 1. Movimiento Atómico de Archivos
Todos los PDFs fiscales deben moverse siguiendo el protocolo en `classifier.py`:
1. Calcular SHA256 del original.
2. Copiar a destino.
3. Verificar SHA256 de la copia.
4. Borrar original SOLO si los hashes coinciden.

### 2. Extracción de Clave de 50 Dígitos
La vinculación depende de la clave de Hacienda de 50 dígitos. Patrones:
- Nombre de archivo: `(?<!\d)\d{50}(?!\d)`
- Contenido: Extracción basada en regex desde el texto o bytes del PDF.

### 3. Estados de Revisión de Hacienda
- **Aceptado**: Procesamiento normal.
- **Rechazado**: Aislado de flujos contables operativos.
- **Sin Respuesta**: Reintento automático en segundo plano vía `atv_client.py`.

---

## 🛠️ Stack Tecnológico
- **GUI**: CustomTkinter (Tkinter moderno).
- **PDF**: PyMuPDF (`fitz`).
- **Datos**: SQLite (clasificaciones), JSON (catálogos/caché).
- **Red**: Pathlib, Shutil (optimizado para latencia de red).
