# DataSifter 2.0 — Phase 1 Implementation Plan

## Summary

Build the Phase 1 native desktop application described in `datasifter2.0_spec.md`: a PyQt6 LDAP client with local JSON profile metadata, OS keyring password storage, isolated asynchronous LDAP operations, TLS options, a DIT workspace, guarded directory mutations, and an offline llama.cpp-based natural-language-to-filter workflow with command review. Phases 2 and 3 are excluded.

The starting folder has no application code, test suite, or project constitution. The specification has section references but no `REQ-XXX` IDs; this plan uses the existing section numbers as requirement references rather than inventing IDs or behavior.

## Decisions

- Scope: Phase 1 only.
- Local AI runtime: llama.cpp, using a model file explicitly selected/configured by the user.
- Profile metadata: JSON under the platform-specific application configuration directory.
- Passwords: OS-native keyring only; never included in profile JSON.
- Validation: offline unit tests and static checks first; live LDAP behavior requires a user-provided directory and is not simulated as a successful connection.
- No RDBMS, SaaS connectors, or synchronization engine in this implementation.

## Implementation Steps and Tasks

### Phase 1: Setup

- [x] T001 [Plan:1.1] Create the Python package, dependency metadata, application entry point, and platform-aware packaging configuration using the specified Python/PyQt6 stack.
- [x] T002 [P] [Plan:1.2] Implement JSON profile configuration and keyring-backed password storage, keeping secret values out of persisted configuration.
- [x] T003 [P] [Plan:1.3] Establish offline tests for configuration paths, profile serialization, and secret-storage boundaries.

### Phase 2: Foundational LDAP and UI

- [x] T004 [Plan:2.1] Build the PyQt6 main window, mode navigation, workspace tabs, status/progress surfaces, and protocol log console.
- [x] T005 [Plan:2.2] Implement worker-based LDAP connection/search operations with per-task connections, cancellation, progress/error reporting, and no network work on the GUI thread.
- [x] T006 [Plan:2.3] Implement LDAP profile transport settings for plain LDAP, StartTLS, LDAPS, strict certificate validation, and optional client-certificate configuration.
- [x] T007 [Plan:2.4] Implement persistent DIT navigation and chunked child loading, then display entry attributes with the operational-attribute toggle.

### Phase 3: LDAP operations and safety

- [x] T008 [Plan:3.1] Implement schema-guided entry creation and Add/Replace/Delete attribute updates.
- [x] T009 [Plan:3.2] Implement confirmed entry deletion with recursive-subtree protection, and rename/move operations with `deleteOldRDN` support.
- [x] T010 [Plan:3.3] Implement LDAP Password Modify Extended Operation support and guard every mutation behind the profile read-only setting.
- [x] T011 [Plan:3.4] Implement certificate trust prompts with SHA-256 fingerprint and certificate details, persist accepted profile trust, and detect fingerprint changes.

### Phase 4: Offline AI command review

- [x] T012 [Plan:4.1] Implement a llama.cpp local inference adapter that sends structural directory schema context, not entry attribute values, and produces LDAP filters.
- [x] T013 [Plan:4.2] Implement an editable command-review surface with LDAP-filter validation, execute gating, and global/per-session confirmation preferences.
- [x] T014 [Plan:4.3] Test that AI requests remain local, sensitive entry values are scrubbed, invalid filters cannot execute, and bypass preferences behave as specified.

### Phase 5: Verification and documentation

- [x] T015 [Plan:5.1] Add user-facing setup, profile, TLS/trust, model configuration, and packaging documentation; package the Phase 1 app as a standalone desktop application.
- [x] T016 [Plan:5.2] Run the available automated test suite and build/package checks; document any live-directory validations that require external infrastructure.

## Requirement Mapping

| Spec section | Plan items | Implementation evidence |
|---|---|---|
| §2.1 Async workers | 2.2 | LDAP worker and UI progress/cancellation tests |
| §2.2 Keyring and local config | 1.2 | Profile/config and secret-storage modules |
| §2.3 TLS, mTLS, and TOFU | 2.3, 3.4 | LDAP TLS/profile and certificate trust handling |
| §3.1–3.2 Phase 1 navigation/workspace | 2.1, 2.4 | PyQt6 window, DIT tree, workspace tabs |
| §3.2 Protocol log | 2.1 | LDAP operation log console |
| §4.1 LDAP CRUD, rename/move, password | 3.1–3.3 | LDAP operation services and guarded UI actions |
| §4.3 Read-only safety | 3.3 | Central mutation guard and tests |
| §5.1 Offline local AI and context scrubber | 4.1, 4.3 | llama.cpp adapter and privacy tests |
| §5.2 Review, validation, confirmation | 4.2, 4.3 | Review UI, validator, preference tests |
| §6 Phase 1 roadmap | 1.1–5.2 | Complete Phase 1 deliverable |
| §7 Packaging | 5.1–5.2 | Packaging config and build verification |

## Project Structure

```text
datasifter2.0_spec.md
plan.md
pyproject.toml
README.md
src/
  datasifter/
    __init__.py
    __main__.py
    config.py
    directory/
    security/
    ai/
    ui/
tests/
```

## Completion

All 16 planned tasks are implemented. The Phase 1 follow-up adds active LDAP socket interruption across LDAP operations, sanitized decoded ldap3 request-dictionary logging where available (with summaries as a fallback), an LDAP filter syntax highlighter, a schema inspector with attribute syntax details, and streamed/cancellable local inference. Model selection records an operator-attested source and SHA-256 digest; inference refuses unapproved or changed model files. The standalone build at version 0.005 and its UI startup smoke test passed before the latest cancellation and logging follow-up. Latest validation passed 92 tests, Ruff, `compileall`, and `pip check`; the BOM generator validated and regenerated 204 components. External LDAP-server interoperability, clean-target TLS, real-model behavior, and macOS/Linux builds remain environment-dependent.

The approved profile usability follow-up adds editing of existing profiles, an explicitly labeled
masked bind-password field, and separate network and authentication test buttons for saved profiles.
Network testing checks TCP reachability without credentials; authentication testing reuses the
configured LDAP/TLS bind and certificate trust flow without populating the directory workspace.
Tests cover credential persistence boundaries, profile identity and pin invalidation, TCP success
and failure, bind failure, missing-password rejection, and StartTLS-before-bind ordering.

Standalone builds automatically reserve versions in increments of 0.001. Explicit overrides are
supported. The shared source version drives package metadata, Qt application version, window title,
and a permanent status-bar label. The packaged version 0.005 was launched and its main window verified.

## Acceptance and Validation

- App starts into a native PyQt6 window without requiring a live directory or model.
- Profile metadata persists in the platform configuration directory; passwords are stored/retrieved only through keyring.
- LDAP network tasks run outside the GUI thread and surface progress, cancellation, and failures.
- LDAP writes are blocked when a profile is read-only.
- AI inference is local-only, and prompt context excludes directory entry values.
- A generated or edited filter is shown for review by default; invalid syntax cannot be executed.
- Offline tests and the configured build/package checks pass. Live LDAP/TLS behavior is reported separately when no test directory is available.
