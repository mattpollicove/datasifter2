# DataSifter project instructions

- Python code uses a `src/` layout. Import application modules as `datasifter...`;
  `pyproject.toml` discovers packages from `src`. The workspace adds `src` to Pylance
  analysis paths; do not add `sys.path` edits, local import shims, `sitecustomize`, or `.pth`
  hooks to work around editor diagnostics. Check the exact interpreter, runtime imports,
  and resolved module paths before diagnosing an import failure. See
  [DEVELOPMENT_ISSUES.md](../DEVELOPMENT_ISSUES.md) for prior import-shadowing issues and
  the pending review of local coding-model import resolution. That model environment was
  unavailable for verification; do not treat its unresolved imports as a confirmed source
  defect or blocker while project-environment imports and tests pass.
- DataSifter's optional inference supports local GGUF files through `llama-cpp-python`;
  this is separate from the model used by a coding agent. Do not add a different model
  framework unless a product requirement explicitly calls for it.
- Before editing, locate the relevant symbols and tests instead of reading or rewriting
  large modules wholesale. In particular, `src/datasifter/ui/main_window.py`,
  `src/datasifter/ui/data_lens_tab.py`, and `src/datasifter/directory/service.py`
  contain broad responsibilities. Refactor only the cohesive responsibility needed for
  the task, preserve behavior with focused tests, and consult
  [DEVELOPMENT_ISSUES.md](../DEVELOPMENT_ISSUES.md) for past regression causes.
- Before adding functionality, consult [BOM.md](../BOM.md) and [bom.json](../bom.json).
  Reuse listed libraries/components where appropriate; do not add dependencies without
  checking existing capabilities.
- After changing functionality, dependencies, or components, regenerate both inventories
  with `python tools\generate_bom.py` and review the changes. Never invent versions or licenses.
- Standalone builds increment the version by 0.001 unless explicitly overridden.
  Follow the [build instructions](../README.md#standalone-build); the build also refreshes the BOM.
- Always include the live LDAP tests when testing: `python -m pytest` runs
  `tests/test_live_ldap.py` and `tests/test_live_datalens.py` against the non-production test
  directories configured in the git-ignored `tests/live_ldap.local.json` (standards-based
  LDAP) and `tests/live_ad.local.json` (Active Directory), with the bind passwords in the OS
  keyring. Do not deselect them, and report a skipped or unreachable target instead of
  treating the run as complete. Plain LDAP and relaxed transport security are acceptable for
  those targets only. Keep writes inside the disposable OU the tests create, and never copy a
  target's address or credentials into tracked files. See
  [CONTRIBUTING.md](../CONTRIBUTING.md#live-ldap-tests).
- `src/datasifter/datalens` is the UI-independent Data Lens engine and is built in phases;
  see [README.md](../README.md#data-lens-engine). Keep it free of PyQt imports, route
  directory access through `LDAPService`, and keep every mutation behind the dry-run and
  confirmation gate.
