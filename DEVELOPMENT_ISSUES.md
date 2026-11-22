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

## QSignalSpy Import Shadowing

- **Problem:** Tests imported `QSignalSpy` from `PyQt6.QtCore`, while the class is provided by `PyQt6.QtTest`. Local `PyQt6.py`, shim packages, and `sitecustomize.py` files attempted to patch the installed Qt package, but shadowed it and caused recursive or incomplete imports.
- **Attempted solution:** Added local shim modules, modified `sys.path` at startup, and used a `.pth` hook to load the shim.
- **Resolution:** Import `QSignalSpy` from the official `PyQt6.QtTest` module and remove the local PyQt6 and startup import overrides. The installed PyQt6 package already provides this utility.

Before:

```python
from PyQt6.QtCore import QSignalSpy
```

After:

```python
from PyQt6.QtTest import QSignalSpy
```

- **Lesson learned:** Use Qt's testing APIs from `PyQt6.QtTest`; avoid shadowing a compiled package to relocate or re-export its modules.

- **Validation finding:** The directory-lens test fixture also used `profile_id="test"`, which violates `DirectoryProfile`'s UUID validation and prevented those tests from reaching their signal assertions. Replaced it with a deterministic valid UUID.

Before:

```python
profile_id="test"
```

After:

```python
profile_id="00000000-0000-0000-0000-000000000001"
```

- **Validation finding:** The shared test fixture creates widgets but did not request pytest-qt's `qapp` fixture. Added `qapp` as a dependency of the `lens` fixture so a `QApplication` exists before Qt widgets are constructed.

Before:

```python
def lens(profile):
```

After:

```python
def lens(profile, qapp):
```

## Create Requests Routed to Search

- **Problem:** Requests entered in the main Directory Lens field were always sent to the local LDAP filter generator, so an explicit user-creation request produced a search instead of an LDAP add.
- **Resolution:** Route explicit `create user` and `create entry` requests to the Directory Lens CRUD command handler. The writable-profile check and confirmation dialog remain in effect; other requests continue through filter generation.

Before:

```python
def _generate_filter(self):
    self._refresh_lens_controls()
    request = self.ai_request.toPlainText()
    # Continue with local filter generation.
```

After:

```python
if self._create_request_kind(request):
    self.directory_lens_tab.submit_command(request)
    return
```

- **Lesson learned:** Route explicit mutation intents to the existing CRUD workflow instead of asking a filter-only model to interpret them.

- **Security note:** The create handler previously logged all attribute values, including `userPassword`. It now logs only the DN and attribute names.

Before:

```python
logger.debug("Creating LDAP entry DN=%s attrs=%s", dn, attrs)
```

After:

```python
logger.debug("Creating LDAP entry DN=%s attribute_names=%s", dn, sorted(attrs))
```

## User Lookup Searches One Attribute

- **Problem:** A request such as `show user mattp` did not match a deterministic parser rule, leaving the model to guess which LDAP attribute represented the user's identifier.
- **Resolution:** Build an exact person lookup across the supported identity attributes present in the directory schema, including `cn`, `uid`, `sAMAccountName`, `userPrincipalName`, `displayName`, `mail`, and `employeeNumber`. Existing search review and the active profile's Base DN continue to control execution and scope.

Before:

```python
structured_filter("show user mattp", attributes)  # None; falls back to model
```

After:

```text
(&(objectClass=person)(|(cn=mattp)(uid=mattp)(sAMAccountName=mattp)...))
```

- **Lesson learned:** For a bare account identifier, query common schema-supported identity attributes together and let the existing directory result view show the candidate entries.

## Directory Disconnect Action

- **Problem:** The main toolbar allowed connecting to a directory but offered no way to end the active session and clear its data from the workspace.
- **Resolution:** Added a Disconnect button that cancels active work, detaches the Directory Lens service, clears session data and schema state, and ignores stale worker callbacks. Saved profiles and credentials are retained.

- **Lesson learned:** Async operation results must be scoped to the connection session that started them so disconnecting cannot repopulate cleared UI state.

## Directory Tools Disabled Until Connected

- **Problem:** Search/filter controls and the Directory Lens tab were available before the connection had completed.
- **Resolution:** Keep filter controls, schema loading, and the Directory Lens tab disabled until a connection succeeds; keep them disabled during connection attempts and disable them again on disconnect.

- **Lesson learned:** A selected or pending profile is not proof of an established directory session.

## Directory Workbench Naming

- **Problem:** The CRUD tab was labeled "Directory Lens," although Directory Lens is the AI filter assistant and the broader directory area also supports browsing and mutations.
- **Resolution:** Name the overall browsing area "Directory Workbench," the CRUD tab "Directory Commands," and reserve "Directory Lens" for AI-assisted search and filtering.

## Directory Command Parser Guidance

- **Problem:** Unrecognized input in Directory Commands only produced a generic parser error, and `show user ...` was not distinguished from a CRUD command.
- **Resolution:** Show parser-accepted create, update, move, and delete examples on parse failure. Recognize `show/find/list/get/search/display user(s)` as a search request and direct users to the Directory Workbench search prompt. A regression test executes each displayed CRUD example through the parser.

## Move Parser and Markdown Diagnostics

- **Problem:** The move parser referenced undefined source and destination matches; Markdown diagnostics also flagged the README alpha notice, a heading without a preceding blank line, and tabs in these issue-log examples.
- **Resolution:** Parse the source DN and destination form explicitly, cover the `relative DN under superior DN` case with a test, format the alpha notice as a heading, add the missing heading spacing, and replace tabs in code examples with spaces.

## Add Attribute to a User by Identifier

- **Problem:** Directory Commands could not interpret a request such as `add telephonenumber to mattp 732 555 1212` as an update.
- **Resolution:** Map phone-number synonyms to `telephoneNumber` and mobile/cell synonyms to `mobile`, search for the user across supported identity attributes, and add the complete value only when exactly one user matches. No-match and ambiguous-match results are reported without modifying an entry.

## Directory Commands Button Labels

- **Problem:** CRUD buttons in Directory Commands used generic verbs that did not identify what was being created, updated, deleted, or moved.
- **Resolution:** Label the actions "Create entry," "Update entry," "Delete entry," and "Move entry," and label the natural-language action "Run command."

## Update JSON Command Validation

- **Problem:** The update JSON test checked only that `modify_entry` was invoked, and a newer identifier-based update test referenced the tab class without importing it.
- **Resolution:** Assert the exact distinguished name and normalized operation/value payload, and import the tab in the identifier-based replace test so its behavior is exercised.

## Directory Commands Target Selection

- **Problem:** Update, Delete, and Move buttons asked users to re-enter a DN, allowing the operation target to diverge from the highlighted entry.
- **Resolution:** Capture the highlighted entry DN, collect operation details, confirm the action against that DN, and perform the operation on the captured entry. Workbench attribute saves also confirm the captured highlighted DN before submitting modifications.

## CRUD Requests Routed Away from Search

- **Problem:** The main request button recognized create requests but sent `add`, `update`, `delete`, and `move` requests through local filter generation.
- **Resolution:** Dispatch all supported CRUD verbs to Directory Commands; only non-mutation requests use filter generation. The add-phone regression verifies the local model worker is not invoked.

- **Parser correction:** Accept `create entry <DN> with attributes {…}` as shown in help, preserve the complete `cn=` RDN, and validate the supplied attributes before treating it as a complete command.

## Directory Commands Tree Refresh

- **Problem:** There was no manual way to reload the Directory Commands tree after external directory changes.
- **Resolution:** Add a Refresh tree action that reloads entries and restores the highlighted DN when it remains in the result.

## NLP-Only Directory Changes

- **Problem:** A separate Directory Commands tab exposed manual CRUD buttons even though directory changes are now intended to be initiated through natural-language commands.
- **Resolution:** Remove the visible tab and route CRUD requests from the Directory Workbench input through the existing confirmed command handler.

## Offscreen CRUD Signal Delivery

- **Problem:** Offscreen CRUD tests failed because synchronous service calls emitted signals before `QSignalSpy.wait()`, test doubles did not accept optional cancellation keywords, and queued callbacks could outlive their widgets.
- **Resolution:** Run service operations synchronously offscreen and deliver signals in that mode, keep background workers in GUI mode, context-bind any offscreen timer callbacks, and align test doubles with the optional service cancellation parameters.

## Attribute-First User Update Prompt

- **Problem:** `update phone number to mattp 732 555 1414` was not recognized and fell through to prompts for a user name and changes JSON.
- **Resolution:** Parse attribute-first update requests, normalize the attribute alias, resolve the identifier, and replace the attribute with the value supplied in the prompt. A regression test verifies no follow-up dialogs are opened.

- **Follow-up:** Also accept `update <attribute> for <user> to <value>` and confirm with a sentence that names the normalized attribute, identifier, and value.

## Local Coding-Model Import Resolution Review

- **Observation:** Local coding models have had difficulty resolving/importing the project's Python modules. In the checked development environment, Pylance's import inventory listed `datasifter` as unresolved even though the selected project `.venv` could import `datasifter` and resolve it to `src/datasifter`. The workspace config now adds `./src` to Pylance analysis paths.
- **Environment note:** The local development-model environment associated with the reported issue was not available for verification during this review. The import discrepancy may be specific to that environment and is not confirmed as an application source defect.
- **Current impact:** Not a release or runtime blocker based on the verified project-environment import and passing test suite. Do not change application imports, add shims, or modify `sys.path` to work around an unverified local coding-model environment.
- **Review required:** Recheck import resolution when the affected local development models and their actual Python/editor environments are available. Capture the selected interpreter, exact failing command or diagnostic, traceback, and resolved module paths before deciding whether a source or environment change is needed.

## Uninterpreted Command Feedback

- **Problem:** When a natural-language directory command could not be interpreted, the error guidance did not repeat the command the user entered.
- **Resolution:** Include the original query in the command guidance and in CRUD clarification dialogs, so users can compare their input with the suggested example.
- **Verification:** Regression tests cover both generic unrecognized commands and CRUD commands that need clarification.

## Directory Lens Renamed to Data Lens with Target Session Commands

- **Change:** Directory Lens is now Data Lens. `ui/directory_lens.py`, `ui/directory_lens_tab.py`, and `tests/test_directory_lens.py` became `ui/data_lens.py`, `ui/data_lens_tab.py`, and `tests/test_data_lens.py`; `DirectoryLens`/`DirectoryLensTab` became `DataLens`/`DataLensTab`. Earlier entries in this file keep the names in use when they were written. The "Directory Commands" and "Directory Workbench" labels are unchanged.
- **Change:** Data Lens accepts typed session commands (`connect to`/`switch to <profile>`, `disconnect`, `list targets`, `current target`), parsed in `session_commands.py` against the whole request so searches such as `list profiles in ou=People` still reach filter generation.
- **Supersedes:** "Directory Tools Disabled Until Connected" disabled the whole lens panel until a connection succeeded. The panel now stays usable so session commands can run while disconnected; the Run button instead refuses searches and directory commands until a session is established, including during and after an incomplete connection attempt.
- **Lesson learned:** A failed connection attempt leaves `_active_profile` set with `_connecting` still true. Gate on both, not on `_active_profile` alone, before running anything against the directory.

## Data Lens Engine Phase 1

- **Change:** Added the UI-independent `datasifter.datalens` package: Pydantic models, DN helpers, and `StandardLdapAdapter` / `ActiveDirectoryAdapter` with dry-run previews and a confirmation gate. The adapters call `LDAPService` rather than opening their own connections. `LDAPService.search` gained `max_entries` so a size-limited search stops paging early. Pydantic is a new runtime dependency.
- **Finding:** ldap3's mock directory returns the base entry itself in a one-level search and enforces neither "parent must exist" nor "no delete of a non-leaf". The children probe therefore asks for two entries and drops the entry itself. Do not rely on the mock to prove directory rules; the live tests do that.
- **Finding:** Active Directory's schema marks `instanceType`, `nTSecurityDescriptor`, `objectCategory`, `objectSid`, `sAMAccountName` and `groupType` as required, but the domain controller supplies them. Treating the schema as authoritative would block every create, so the AD adapter excludes them. Verified against the test domain.
- **Limitation:** The test domain controller has no TLS certificate (StartTLS answers "Error initializing SSL/TLS", port 636 resets). Writing `unicodePwd` is covered by mock tests only until it has one; set `ldaps_port` in `tests/live_ad.local.json` to enable the live test.

## Modal Dialog Stalled an Unattended Test Run

- **Problem:** `test_startup_and_disconnected_lens_do_not_contact_ldap` submits an incomplete delete command, which opens the modal "Command needs clarification" dialog. Nothing in the test closed it, so the suite waited for someone to dismiss the dialog by hand.
- **Resolution:** The test now replaces `CommandClarificationDialog.exec` with an immediate rejection.
- **Lesson learned:** A test that can reach a modal dialog must patch its `exec`. Run the full suite with `-o faulthandler_timeout=120` when a run stalls; the dump names the blocking call.

## Data Lens Engine Phase 2

- **Change:** Added `datalens.aliases` (backend-aware concept registry), `datalens.resolver` (terms to attributes, names to entries, requests to plans or clarification questions) and `datalens.schema`. The adapters gained group and container filters, account-state filters and changes, `known_attributes`, and `execute(plan)`.
- **Problem found in existing code:** Any search whose filter contained an extensible match, such as Active Directory's `(userAccountControl:1.2.840.113556.1.4.803:=2)` or the `(ou:dn:=People)` location match, failed with "Assertion must contain a valid LDAP comparison operator". ldap3 records such assertions in its own multi-line `ExtensibleMatch:` form, and the protocol log's redaction validated that text as RFC 4515 and raised inside the logging step.
- **Resolution:** Redaction no longer raises on text it cannot parse; the log then shows the filter as the caller wrote it, with values redacted. A regression test covers the ldap3 form.
- **Lesson learned:** Logging must never be able to fail the operation it describes.
- **Finding:** A server defines only some of the account-status attributes of the various LDAP server families, and ldap3 refuses to request an attribute the schema does not define. Lists such as `account_status_attributes` must be filtered through `known_attributes` first.
- **Finding:** With `max_options` matches fetched, one returned entry is unique only when the search was not truncated. The first version treated a truncated single result as the answer; a test with `max_options=1` caught it.
- **Finding:** ldap3's mock ignores the new RDN of a ModifyDN that also names a new superior. Rename-and-move in one operation is verified by the live tests instead.

## Data Lens Engine Phase 4

- **Change:** Mutation proposals require explicit review and confirmation; the reviewed preview
  is checked again immediately before submission. A separate local JSON-lines audit records
  metadata only. LDAP cannot make the preview and write atomic, and recursive deletion can
  partially complete.
- **Lab update:** The AD lab now presents a certificate on LDAPS port 636. Its local test
  configuration can enable that port. The live test fixture now captures the target address
  separately from the TLS hostname, so the certificate name resolves to the configured target.
