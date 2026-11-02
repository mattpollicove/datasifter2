# DataSifter project instructions

- Before adding functionality, consult [BOM.md](../BOM.md) and [bom.json](../bom.json).
  Reuse listed libraries/components where appropriate; do not add dependencies without
  checking existing capabilities.
- After changing functionality, dependencies, or components, regenerate both inventories
  with `python tools\generate_bom.py` and review the changes. Never invent versions or licenses.
- Standalone builds increment the version by 0.001 unless explicitly overridden.
  Follow the [build instructions](../README.md#standalone-build); the build also refreshes the BOM.
