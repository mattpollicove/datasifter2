# Contributing to DataSifter

Contributions are welcome! DataSifter is **ALPHA / work in progress**. Read the
[current progress and roadmap](./README.md#current-progress) before starting.
Suggestions, comments, feature requests, and bug reports are welcome through
[GitHub Issues](https://github.com/mattpollicove/datasifter2/issues); no code contribution
or test plan is required to share feedback. For substantial changes, open an issue first
to agree on scope and avoid duplicate work.

## Before pushing or requesting review

1. **Include a test plan.** State the behavior being changed, expected results, relevant
   regression cases, failure/cancellation paths, and how to reproduce the change. For UI
   work, include keyboard behavior and enabled/disabled states. For directory mutations,
   cover read-only protection and confirmations using a disposable test directory.
2. **Include proof of testing.** Record the exact commands, results, environment, and commit
   tested in the pull-request description. Attach concise sanitized output, screenshots,
   or manual-test steps and outcomes as appropriate. Do not describe planned tests as run.
   Clearly identify anything not tested and why.
3. **Update documentation with the change.** Update setup/usage instructions, limitations,
   current progress, and roadmap status when affected. Update the specification or plan if
   the intended scope changes. For documentation-only work, state that runtime tests are
   not applicable and record the links/examples you checked.
4. **Update the inventories when required.** Consult [BOM.md](./BOM.md) and
   [bom.json](./bom.json) before adding functionality or dependencies. Reuse existing
   capabilities. After changing functionality, dependencies, or components, regenerate both
   inventories and review their diff; never invent versions or licenses.
5. **Review the diff.** Keep changes focused. Do not commit credentials, local profiles,
   directory entry data, private certificates/keys, GGUF weights, virtual environments, or
   generated builds. Preserve applicable license notices.

## Development and validation

From the project root in PowerShell, create a virtual environment if needed, then install
the declared extras for your work:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,bom]"
```

Real-model testing additionally requires the `local-ai` extra and an independently
approved, compatible GGUF file; standalone packaging requires the `packaging` extra.
See the [setup and model instructions](./README.md#install-and-run).

Run focused tests during development, then the suite before submitting code:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_filters.py tests\test_local_ai.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests tools
.\.venv\Scripts\python.exe tools\generate_bom.py
git diff --check
git diff --stat
```

Use the tests relevant to your change; the focused command above is a parser/AI example.
If checks expose unrelated existing issues, report them separately rather than hiding
them or expanding scope without agreement.

Offline tests do not establish live LDAP interoperability, TLS behavior on a clean target,
or real-model accuracy. For those changes, report the directory product/version, transport,
OS/Python version, or model/quantization and hardware used. Use only non-production test
data, redact sensitive details, and explicitly identify mocked versus live results.
Standalone builds reserve a version increment; follow the
[build instructions](./README.md#standalone-build), and report packaged-app validation
separately from source tests.

## Suggested pull-request evidence

```text
Summary:
Related issue / roadmap item:

Test plan:
- Scenario, steps, and expected result
- Regression and error-path cases

Testing performed:
- Commit tested:
- Environment:
- Exact command or manual steps:
- Actual result and sanitized evidence:
- Mocked versus live dependencies:
- Not tested / known limitations:

Documentation:
- Updated files and affected behavior, or why no update applies

BOM:
- Regenerated and diff reviewed, or why not applicable
```

The [MIT license](./LICENSE) covers original DataSifter code. Contributions must be
compatible with it, while preserving the separate
[third-party licensing obligations](./THIRD_PARTY_NOTICES.md). Do not include model
weights or third-party code without appropriate redistribution rights and notices.
