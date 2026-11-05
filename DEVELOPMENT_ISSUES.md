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
