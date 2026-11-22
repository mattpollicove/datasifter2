# DataSifter AI Build Prompts

Copy and use these prompts in order. They are designed to build or carefully rebuild
DataSifter in small, verifiable slices instead of asking an AI to generate the entire
application at once.

## Grounded project scope

This prompt set is based on the current repository:

- DataSifter is an alpha, local-first PyQt6 desktop application. The implemented product
  scope is Phase 1: LDAP directory workspaces and optional offline natural-language
  LDAP-filter assistance. See [README.md](./README.md) and
  [plan.md](./plan.md).
- The broader [datasifter2.0_spec.md](./datasifter2.0_spec.md) includes future RDBMS and
  provisioning ideas. Those are roadmap items, not current acceptance criteria. Do not
  build them unless they are explicitly requested.
- The package requires Python 3.10+ and currently declares PyQt6, ldap3, keyring, and
  cryptography. `llama-cpp-python` is optional under the `local-ai` extra. Consult
  [pyproject.toml](./pyproject.toml), [BOM.md](./BOM.md), and [bom.json](./bom.json)
  before proposing libraries or versions.
- The project uses a `src/` layout with setuptools package discovery configured in
  `pyproject.toml`. A previous import-shadowing incident documented in
  [DEVELOPMENT_ISSUES.md](./DEVELOPMENT_ISSUES.md) was caused by local PyQt shims and
  startup import overrides; it was resolved by removing those overrides and importing
  Qt testing APIs from their official module. Do not reintroduce shims, `sitecustomize`,
  `.pth` import hooks, or `sys.path` edits to mask editor/import-resolution problems.
- Do not conflate AI coding agents' Python-package imports with DataSifter's inference
  model support. The app deliberately accepts local GGUF models through `llama-cpp-python`;
  it does not load PyTorch checkpoints, `.safetensors`, or arbitrary Python models.
- Import reports depend on the interpreter and editor configuration. In one checked
  workspace, Pylance marked `datasifter` unresolved while the selected project `.venv`
  successfully imported it from `src/datasifter`. Treat that as a tooling discrepancy to
  diagnose, not proof that source imports are broken or a reason to add import hacks.
- The existing implementation is useful evidence of behavior, but should not be copied
  blindly. Preserve its tested contracts while keeping responsibilities clear and avoiding
  another oversized UI or service module.

## Prompt 0 - Master instructions

Use this as the standing instruction for the coding agent throughout the build:

```text
Act as a senior Python desktop-application engineer. Build DataSifter as a local-first
PyQt6 LDAP client, using the repository and its tests as behavioral evidence. Work in
small vertical slices; do not generate or rewrite the whole application in one pass.

Scope:
- Implement the current Phase 1 LDAP application only.
- Treat README.md and the current tests as evidence of implemented behavior. Treat
  datasifter2.0_spec.md roadmap sections as future work unless I explicitly authorize them.
- Do not add RDBMS support, provisioning, cloud inference, remote AI, or unrelated features.
- Before coding, inspect the relevant source, tests, pyproject.toml, BOM.md, and bom.json.
  Reuse existing dependencies and helpers; do not invent dependency versions or licenses.
- Establish which Python interpreter runs the task and compare it with the editor/Pylance
  interpreter before diagnosing missing imports. For import issues, verify the installed
  package location and a minimal import using that exact interpreter, and inspect the
  `src/` package-discovery configuration. Report editor and runtime results separately.
  Do not add `sys.path` mutations, local shims, `sitecustomize`, or `.pth` hooks to silence
  unresolved-import diagnostics; fix the environment/project configuration only after
  evidence identifies the cause.
- Keep code navigation tractable: inspect the smallest relevant symbol range and its tests
  before editing. `ui/main_window.py`, `ui/data_lens_tab.py`, and
  `directory/service.py` are large legacy modules; avoid loading or rewriting them wholesale.
  Extract a cohesive responsibility only when required by the approved slice, preserving
  existing contracts and tests.

Architecture and implementation:
- Separate UI presentation, LDAP operations, configuration/credentials, transport trust,
  local model handling, and pure filter logic. Keep network and model work off the GUI thread.
- Preserve testable service boundaries and inject stores, vaults, workers, and other external
  dependencies where the current code already supports it.
- Prefer straightforward, typed Python and small cohesive modules. Refactor large UI files
  only as needed for the requested slice; preserve behavior and tests during refactoring.
- Make each change surgical. Do not conceal failures with broad catches, silent defaults,
  or success-shaped fallbacks. Surface actionable errors using repository conventions.
- Do not rewrite unrelated code or overwrite user changes.

Non-negotiable safety and privacy:
- Passwords and private-key passphrases belong in the operating-system keyring, never
  profile JSON, logs, test snapshots, or AI prompts.
- Keep LDAP TLS certificate and hostname validation strict. TOFU must be explicit,
  profile-scoped, fingerprint-verified, and must stop on unexpected certificate rotation.
  Never "fix" trust errors by disabling certificate validation.
- Enforce read-only profiles at the service boundary, not only by disabling UI controls.
  Require confirmations for directory writes as configured. Cancellation after a write is
  submitted does not prove rollback; tell the user to verify directory state before retrying.
- Sanitize protocol and diagnostic logs. Never log credentials, attribute values, bind
  payloads, or unredacted filter assertions.
- Data Lens inference must remain local and GGUF-only. Do not send LDAP entries,
  attribute values, credentials, or profile data to the model. Supply only reduced schema
  names and object classes. Treat local model source attestation and SHA-256 verification
  as integrity safeguards, not publisher authentication.
- Distinguish the model used by a coding agent from the optional inference model used by
  the application. Do not install or add support for another model framework merely because
  a coding agent has trouble navigating/importing this Python project.
- Prefer deterministic handling for simple filter requests. Validate and schema-check every
  generated filter before it can be reviewed or executed. Keep explicit CRUD commands on
  their deterministic, confirmed path; do not let model output perform writes.

Quality gates:
- Add or update tests for success, validation, failure, cancellation, privacy, and safety
  behavior touched by the change. Use fakes/mocks for offline tests; do not claim a live
  LDAP server or real model was tested unless it actually was.
- Run the smallest relevant test set, then the full test suite and Ruff when appropriate.
- Update directly affected documentation. After functionality, dependency, or component
  changes, regenerate and review BOM.md and bom.json using the repository instructions.
- Do not run a standalone build casually: its build spec increments the version by 0.001.
  Build only when requested or required for release validation, and report that evidence
  separately from source tests.
- Report changed files, exact checks run and results, remaining risks, and anything not
  verified. Never report planned checks as completed.
```

## Prompt 1 - Read-only analysis and requirements

Start each new project or major rebuild with this prompt. Do not let the agent start coding
until the scope and acceptance criteria are clear.

```text
Analyze the DataSifter repository without changing files.

Read README.md, datasifter2.0_spec.md, plan.md, CONTRIBUTING.md, pyproject.toml, BOM.md,
bom.json, the source package, and the tests. Separate findings into:
1. Implemented and tested behavior.
2. Specified behavior that is not implemented or is roadmap-only.
3. Ambiguities or conflicts that require a decision.

Map the current package boundaries and the user-visible flows for:
- profile creation/editing, credentials, connection checks, and directory connection;
- LDAP browsing, search, schema inspection, attribute editing, and mutation operations;
- transport security, certificate trust, logging, worker execution, cancellation, and errors;
- Data Lens filter generation, model management, review, and execution.

For each flow, cite the source/test file and symbol or test that supports the finding. Do not
infer behavior from a roadmap description when source or tests show otherwise. Do not invent
requirements, measured performance, compatibility claims, or live-server test results.

When reporting Python import/model problems, distinguish:
- editor/Pylance diagnostics from imports executed at runtime;
- the interpreter selected by the editor from the interpreter used by tests or commands;
- DataSifter's `datasifter` package imports from the optional `llama_cpp` dependency;
- the application's GGUF inference models from models used by external coding agents.

Check `DEVELOPMENT_ISSUES.md` for prior import-shadowing attempts and their resolution.
Verify import behavior with the exact selected interpreter, including resolved module
locations. The current project uses a `src/` layout; do not recommend local package shims,
startup hooks, or manual `sys.path` changes without evidence that the supported packaging
configuration is defective.

Return a concise behavior inventory, an explicit Phase 1 in/out-of-scope list, proposed
acceptance criteria, and questions that block implementation. Make no code or documentation
changes. Wait for approval before planning implementation.
```

## Prompt 1A - Diagnose Python imports and model confusion

Use this when an editor, coding agent, test, or user reports Python import errors or says
that a "Python model" will not load. Diagnose first; do not change code unless the evidence
proves a code defect.

```text
Diagnose this DataSifter Python import/model issue without editing files:

Observed symptom and full traceback/diagnostic: [paste exact evidence]
Command used and working directory: [exact command and directory]
Python interpreter path: [exact path, if known]
What "model" means here: [coding-agent model / DataSifter GGUF inference model / Python package]

Establish the editor-selected interpreter and the interpreter used by the failing command.
Inspect pyproject.toml, the src/ package layout, relevant imports, and DEVELOPMENT_ISSUES.md.
Using the exact interpreter in question, report:
- Python executable/version;
- whether `datasifter`, the implicated internal module, and (if relevant) `llama_cpp`
  import successfully;
- each imported module's resolved `__file__` path;
- whether the dependency is declared/optional and installed in that environment;
- the exact Pylance diagnostic, if any, separately from runtime results.

Do not assume an editor unresolved-import diagnostic means runtime import failure. Do not
assume a successful runtime import means Pylance is configured correctly. Do not add
PyQt/Python shims, sitecustomize modules, .pth hooks, or sys.path workarounds. Do not
confuse external coding-agent models with DataSifter's supported local GGUF inference
models. The application does not currently support PyTorch, safetensors, or arbitrary
Python model formats.

Return the confirmed cause, evidence, and smallest corrective action. If evidence does not
identify a code defect, say so and recommend the appropriate interpreter/editor/package
configuration check instead of changing application source.
```

## Prompt 2 - Architecture and implementation plan

Use after approving Prompt 1's scope:

```text
Using the approved Phase 1 requirements and your repository analysis, propose an
implementation plan. Do not edit files yet.

Organize work into independently testable vertical slices. For each slice list:
- user-visible behavior and acceptance criteria;
- modules/interfaces to add or change, with responsibility boundaries;
- tests to add/update, including error and cancellation paths;
- dependencies on earlier slices;
- documentation and BOM effects, if any.

Use the existing stack and dependency set unless a concrete requirement cannot be met with
current capabilities. Identify any proposed dependency and the BOM evidence supporting it;
do not guess a version. Preserve current public behavior unless an approved requirement
intentionally changes it. Keep RDBMS and provisioning out of this Phase 1 plan.

Prefer a minimal, reviewable sequence: app/config foundations; LDAP service and transport;
worker/cancellation integration; directory UI and guarded writes; local filter assistance;
packaging/documentation hardening. Adjust this sequence only when repository evidence
requires it. Call out risky seams and how tests will verify them.

Return the ordered plan and stop for approval.
```

## Prompt 3 - Build a vertical slice

Use once per approved plan slice. Replace the bracketed values:

```text
Implement only this approved DataSifter slice:

Slice: [name]
Acceptance criteria: [criteria]
In-scope modules: [paths]
Relevant existing behavior/tests: [paths or symbols]

Before editing, inspect the current implementations and tests for these paths. Reuse
existing helpers and dependency capabilities. Preserve unaffected behavior and avoid
unrelated cleanup. Keep the change cohesive and type-safe; do not add dependencies unless
the approved plan and BOM inspection justify them.

Add/update tests first or alongside the implementation. Cover normal results, invalid input,
operation failure, cancellation where relevant, and safety/privacy boundaries touched by
this slice. For UI work, cover visible state and enabled/disabled controls. For writes,
cover read-only enforcement and confirmation. Use offline fakes unless live infrastructure
was explicitly provided.

Implement the complete slice, run its focused tests and Ruff on touched code, and fix
failures caused by this change. Update directly related documentation. If functionality,
dependencies, or components changed, regenerate BOM.md and bom.json with
`python tools\generate_bom.py` and review their diffs.

At completion report: changed files, acceptance criteria met, exact commands/results,
documentation/BOM updates, and any unverified behavior. Do not claim a live LDAP,
real-model, cross-platform, or packaged-app validation unless it was actually performed.
```

## Prompt 4 - LDAP service and transport slice

Use for the LDAP core, adapting scope to the approved plan:

```text
Implement the approved LDAP service/transport slice for DataSifter.

Keep protocol operations in the service layer, not Qt widgets. Use isolated LDAP connection
instances per operation. Support only approved profile transports and authentication modes;
preserve strict TLS validation, hostname matching, configured CA/client certificates, and
the explicit profile-scoped TOFU workflow. Make network and authentication checks distinct:
the network check must not send credentials, while the authentication check must not browse
entries.

Validate LDAP filters before sending them. Preserve paged DIT/search behavior, schema access,
entry reads, and approved write operations. Centralize read-only enforcement in the service.
Redact bind data, filter values, attribute values, and password-change payloads from logs.
Cancellation must interrupt blocking work where possible, and write cancellation messages
must not imply that a submitted operation was rolled back.

Use service-level tests with mocked LDAP connections and explicit error responses. Test TLS
ordering, filter rejection, paging, redaction, read-only rejection, write failure, and
cancellation. Do not use a successful mocked test as evidence of live LDAP interoperability.
```

## Prompt 5 - Desktop UI and worker slice

```text
Implement the approved PyQt6 UI/worker slice without putting blocking work on the GUI thread.

Keep widgets responsible for presentation and user intent; delegate LDAP/model work to
workers and services. Return results and progress through Qt signals. Maintain usable busy,
cancel, error, and disconnect states. Prevent stale worker results from changing a newer
connection/session. Preserve pending-edit discard checks, keyboard behavior, read-only
attributes, operational-attribute visibility, and actionable error messages where relevant.

Directory mutations must have explicit confirmations and remain blocked for read-only
profiles even if a UI control is invoked programmatically. Keep confirmation preferences
persisted as designed. For cancellation of submitted writes, clearly communicate uncertainty
and advise verification before retry.

Add pytest-qt coverage for the user-visible flow, control enablement, cancel/error paths,
confirmation decisions, and pending edits. Use fake service/worker dependencies; tests must
not require a live directory.
```

## Prompt 6 - Data Lens and local model slice

```text
Implement the approved Data Lens slice as a local-only LDAP filter assistant.

Use the deterministic structured-filter path for unambiguous supported requests; return to
the local model only when deterministic parsing cannot safely represent the request. Pass
only relevant schema object-class and attribute names to inference, never directory entry
values. Keep schema context bounded to fit the configured context window.

Accept only local GGUF files with a valid GGUF header and an approved SHA-256 digest.
Preserve the operator-recorded provenance/source-attestation step and explain that it is not
publisher authentication. If model downloads are in scope, use only pinned catalog metadata,
HTTPS, expected size/header/hash checks, cancellation, and safe temporary-file cleanup; never
overwrite an existing model.

Stream output on a worker. Validate LDAP filter syntax, normalize attribute names against
the connected schema, and reject invalid or unknown attributes. Keep generated filters
editable and reviewed before execution by default. Simple search assistance must not become
a path for model-generated LDAP writes; CRUD commands stay on deterministic parsing and
confirmed operations.

Test with fake inference and mocked downloads. Assert that no entry values or secrets enter
the prompt; test deterministic parsing, fallback, malformed output, unknown attributes,
location clauses, model hash changes, cancellation, and review gating. Do not claim real
model accuracy or performance from fake-model tests.
```

## Prompt 7 - Final review and release evidence

```text
Review the completed DataSifter changes against the approved scope and current repository
conventions. Do not make unrelated changes.

Verify:
- every approved acceptance criterion maps to implementation and a test;
- profile secrets remain in the OS keyring and logs/prompts are sanitized;
- TLS validation and TOFU rotation checks remain fail-closed;
- read-only checks and write confirmations cannot be bypassed through UI-only state;
- blocking LDAP/model work does not run on the GUI thread;
- cancellation and error paths do not claim rollback or success without evidence;
- Data Lens remains offline, GGUF-only, schema-only, and filter-validated;
- future RDBMS/provisioning work was not accidentally included.

Run the relevant checks, then the repository's full source checks:
`.\.venv\Scripts\python.exe -m pytest -q`
`.\.venv\Scripts\python.exe -m ruff check src tests tools`
`git diff --check`

If functionality, dependencies, or components changed, regenerate BOM.md and bom.json
with `python tools\generate_bom.py` and review the diff. Do not run a standalone build
unless release validation was requested; the build reserves a new 0.001 version.

Return a concise verification report listing exact commands and results, evidence gaps,
and any live LDAP, real-model, platform, or packaging checks still required. Never infer
that a check passed from its being listed in a plan.
```

## Repository reference map

Use these files as initial navigation, then follow actual imports and tests:

| Concern                               | Current starting points                     |
| ------------------------------------- | ------------------------------------------- |
| App startup and                       | `src/datasifter/__main__.py ` ,             |
| diagnostics                           | `src/datasifter/diagnostics .py`            |
| Profiles, preferences, keyring        | `src/datasifter/config.py`                  |
| LDAP operations and protocol          | ` src/datasifter/directory/s ervice.        |
| logs                                  | py`                                         |
| Qt worker execution                   | `src/datasifter/directory/w orker.py `      |
| Filter parsing/validation and schema  | ` src/datasifter/directory/f                |
| context                               | ilters.py`                                  |
| Certificate details and TOFU          | ` src/datasifter/security/ce rtifica        |
| trust                                 | tes.py`                                     |
| Local GGUF model/hash/inference       | `src/datasifter/ai/local_mo del.py`         |
| Pinned model catalog/download         | `src/datasifter/ai/model_ca talog.py `      |
| Main desktop, profile/review dialogs, | ` src/datasifter/ui/main_win                |
| model lifecycle                       | dow.py`                                     |
| Directory browsing                    | `src/datasifter/ui/data_lens.py`,           |
| and operations UI                     | `src/datasifter/ui/data_lens_tab.py`        |
| Model/settings                        | `src/datasifter/ui/model_di alog.py ` ,     |
| dialogs                               | `src/datasifter/ui/settings _dialog .py`    |
| Regression tests                      | `tests/`                                    |

The main window, Directory Commands UI, and LDAP service are large modules; search for the
specific symbol and read only its relevant implementation and tests before editing. These
are navigation hints, not a substitute for source inspection. See [DEVELOPMENT_ISSUES.md](./DEVELOPMENT_ISSUES.md)
for prior development failures and resolutions, and [CONTRIBUTING.md](./CONTRIBUTING.md)
for the contribution, testing, documentation, and BOM checklist.
