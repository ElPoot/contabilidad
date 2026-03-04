# App 3 Guardrails - Strict Independence Rules

**CRITICAL: These rules prevent breaking App 3 independence. Any violation must be reverted immediately.**

## ❌ FORBIDDEN ACTIONS

```python
# ❌ FORBIDDEN: Import from App 1
from facturacion_system import ...
import facturacion_system

# ❌ FORBIDDEN: Import from App 2
from facturacion import ...
import facturacion

# ❌ FORBIDDEN: Modify files outside app3/
APP 1/facturacion_system/...  ← HANDS OFF
APP 2/facturacion/...         ← HANDS OFF

# ❌ FORBIDDEN: Use their functions directly
self.manager.parse_decimal_value(...)  # from App 2
CRXMLManager from App 2  # from App 2
```

## ✅ ALLOWED ACTIONS

```python
# ✅ OK: Import from within app3/
from app3.core.iva_utils import ...
from app3.core.xml_manager import ...
from app3.core.classifier import ...

# ✅ OK: Read files to understand logic (NEVER import)
# Use Read tool to view APP 2/facturacion/ui_main.py
# Then replicate the logic natively in app3/

# ✅ OK: Use external packages
import pandas as pd
import openpyxl
import requests
import customtkinter as ctk
from pathlib import Path
from decimal import Decimal
```

## 📝 Checklist Before Every Commit

- [ ] Only modified files inside `app3/`?
- [ ] No imports from `facturacion_system` or `facturacion`?
- [ ] No new dependencies on App 1 or App 2?
- [ ] All logic replicated natively in App 3?
- [ ] Tests pass?
- [ ] Git diff shows only `app3/` changes?

## 🚨 If Violation Detected

1. **Immediately stop work**
2. **Run:** `git reset --hard HEAD~1`
3. **Review:** What files were modified outside app3/?
4. **Fix:** Reimplement ONLY in app3/

## Replicated Components (Already Done)

These are examples of logic replicated from App 2 into App 3:

| Component | Location in App 3 | Notes |
|-----------|-------------------|-------|
| IVA parsing | `app3/core/iva_utils.py` | parse_decimal_value(), validate_iva_sum() |
| XML parsing | `app3/core/xml_manager.py` | CRXMLManager class |
| PDF scanning | `app3/core/factura_index.py` | FacturaIndexer class |
| Decimal handling | `app3/core/iva_utils.py` | decimal_to_local_text(), etc. |

## Pattern: How to Replicate Logic from App 2

1. **NEVER do:** `from APP_2.facturacion.module import SomeFn`
2. **DO this instead:**
   - Read the App 2 source using Read tool
   - Understand the logic
   - Write equivalent code in `app3/core/`
   - Create native App 3 function

## Emergency Contacts

- **Violation detected?** Revert immediately with `git reset --hard`
- **Need clarification?** Ask user before modifying anything outside app3/
- **Memory check?** This file: `APP3_GUARDRAILS.md`
