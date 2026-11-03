# Development Issues

## BOM Test Requires the Packaging Extra

- **Problem:** The documented development install omitted PyInstaller, but `tests/test_bom.py` imports `PyInstaller.utils.hooks`. Running the full test suite after following the documented install failed with `ModuleNotFoundError: No module named 'PyInstaller'`.
- **Attempted solution:** Installed the declared `packaging` extra in the virtual environment and reran the suite.
- **Resolution:** Included the `packaging` extra in the contributor development setup command. The full suite then passed.

Before:

```powershell
python -m pip install -e ".[dev,bom]"
```

After:

```powershell
python -m pip install -e ".[dev,bom,packaging]"
```

- **Lesson learned:** Keep documented test-environment extras aligned with imports used by tests, including BOM and packaging tests.
