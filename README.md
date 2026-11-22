# DataSifter 2.0

## ALPHA CODE / WORK IN PROGRESS

Experimental, actively developed software; not production-ready. Features, configuration
formats, and behavior may change. Test against a non-production directory and keep backups
before trying write operations.

Original code is [MIT-licensed](./LICENSE). Dependencies and models have separate licenses;
in particular, the installed PyQt6 distribution is GPL-3.0-only. See
[third-party licensing obligations](./THIRD_PARTY_NOTICES.md) before distributing the app.
The **Help → About DataSifter** dialog includes the [privacy notice](./PRIVACY.md), the full
MIT license, and a link to GitHub Issues for support.

DataSifter is a local-first LDAP desktop client. This implementation targets Phase 1 of
[`datasifter2.0_spec.md`](./datasifter2.0_spec.md): LDAP directory workspaces and an optional
offline llama.cpp assistant.

**AI use:** Data Lens optionally uses a user-approved local GGUF model through llama.cpp
to produce validated `search`, `proposed_mutation`, or `clarify` intents. Inference runs on
your machine and uses request text plus structural schema names, never directory row values.
Searches follow the existing AI confirmation setting (on by default). Mutation intents require
a typed dry-run preview and explicit user confirmation; the model cannot apply changes.
For AI-assisted software development practices, see
[AI_DEVELOPMENT.md](./AI_DEVELOPMENT.md).

Data Lens supports **local GGUF model files only**, loaded through
`llama-cpp-python`. Selection and loading reject other extensions and files without a GGUF
header, even if renamed to `.gguf`. A GGUF header alone does not establish model compatibility
or authenticity; llama.cpp must support the model architecture, and source approval and
SHA-256 verification still apply. Ollama, remote inference services, and other model formats
are not supported.

## Current progress

Status as of **2026-11-22**: **Phase 1 LDAP functionality and Data Lens internal phases 1-5
are implemented and under active alpha testing**. This is not a production-readiness or
interoperability certification.
The [implementation plan](./plan.md) records the original Phase 1 work; the
[specification](./datasifter2.0_spec.md) describes the broader vision, not a list of
features all available today.

### What's working in the current implementation

| Area | Implemented capabilities |
| --- | --- |
| Profiles and credentials | Create/edit/delete profiles, network and authentication checks, local JSON preferences, OS keyring integration, read-only profiles |
| Directory Workbench | Paged DIT browsing, LDAP filter searches, attribute editing, schema-guided entry creation, delete, rename/move, password modification, validated LDIF import/export |
| Transport and safety | Plain LDAP warnings, StartTLS/LDAPS, custom CA/client certificates, TOFU approval and rotation checks, write confirmations |
| Schema inspector | Object-class filtering with inherited attributes and progressive attribute-name search |
| Data Lens | Local GGUF selection/download/approval, integrity checks, model reuse, schema-validated structured intents with one JSON repair attempt, reviewed searches, mutation dry-run previews, typed target session commands (connect, switch, disconnect, list targets, current target) |
| Data Lens engine (phases 1-5) | UI-independent `datasifter.datalens` package: typed intents, backend-aware aliases, confirmed previews, read-only protection, sanitized audit events, structured help and actionable diagnostics |
| Usability | Grouped settings, persistent AI enable/disable, Enter-to-generate, operational-attribute italics, search highlighting, cancellation and sanitized protocol logs |
| Packaging | Windows standalone build workflow, version increments, generated bill of materials |

These features have offline automated coverage; availability in the implementation does
not mean every operation has been verified against a live server or every listed model.

### Testing evidence and current limitations

- Most recent full-suite validation: **536 passed, 1 skipped** on Windows on 2026-11-22.
  That run skipped the AD password-over-LDAPS test because its port was not configured;
  a subsequent targeted run passed against the lab's LDAPS endpoint.
- Automated coverage includes configuration, LDAP service behavior, filters, certificates,
  workers, Qt UI interactions, model approval/download safeguards, and BOM generation.
  Model inference tests use fakes where appropriate; passing them is not a real-model benchmark.
- Live LDAP-server interoperability, end-to-end TLS/client-certificate behavior on clean
  machines, and real-GGUF accuracy/performance still require environment-specific testing.
- The previous Windows standalone build predates recent source changes. The current source
  has not been repackaged or revalidated as a standalone release; macOS/Linux builds are
  not certified.
- Natural-language interpretation can be wrong. Review search scope and every mutation preview;
  complex requests may require rephrasing.

## Roadmap

The ordering below describes direction, not release dates or a guarantee of delivery.

| Stage | Status | Next work |
| --- | --- | --- |
| Phase 1: LDAP alpha stabilization | Implemented; validation and refinement ongoing | Live LDAP/TLS interoperability matrix, real-model accuracy/performance evaluation, parser regression cases, usability/accessibility refinements, fresh standalone builds and clean-target checks |
| Phase 2: RDBMS expansion | Planned; not implemented | Database drivers and explorer, editable data grids, SQL console, refresh behavior, local SQL assistance |
| Phase 3: Provisioning | Planned; not implemented | Provisioning field mappings, explicit create/update/deactivate actions, dry-run previews and audit logs; target integrations to be defined |

AI support remains local GGUF through llama.cpp. Remote inference, Ollama integration,
automatic model training, and a font picker are not current features.

## Contributions welcome

Suggestions, comments, feature requests, and bug reports are welcome through
[GitHub Issues](https://github.com/mattpollicove/datasifter2/issues).
Feedback does not require a code contribution or test plan.

Bug reports, reproducible parser examples, documentation fixes, and code contributions
are welcome. **Code contributions must include a test plan, evidence of testing, and
documentation updates where applicable before pushing or requesting review.**
See [CONTRIBUTING.md](./CONTRIBUTING.md) for the checklist, commands, and evidence format.

## Requirements

- Python 3.10 or newer
- A supported desktop environment for PyQt6
- An LDAP v3 directory for live directory operations
- An optional local GGUF model file and the `local-ai` extra for AI filter generation

## Install and run

```powershell
python -m pip install -e ".[dev,local-ai]"
python -m datasifter
```

On Windows, virtual-environment activation is optional. If PowerShell reports an
execution-policy error, run the application directly without changing that policy:

```powershell
.\.venv\Scripts\python.exe -m datasifter
```

Directory changes are initiated from the natural-language request field in the
Directory Workbench. Connect to a writable profile, enter a create, add, update,
delete, or move command, and confirm the requested operation.

The llama.cpp package may need a platform-specific build toolchain. To run without AI support,
install `-e ".[dev]"`; AI actions will report that the optional dependency or model is missing.
**Data Lens** (formerly Directory Lens) is the request field in the Directory Workbench. It
translates natural-language requests into validated search, proposed-mutation, or clarification
intents with the optional local model, and manages the target session. Open **Settings → Application settings**
for grouped **Data Lens**, **Safety**, and **Appearance** options. Disable Data Lens
to hide its workspace panel and release the loaded model; re-enable it from the same screen.
The approved model file is retained, and manual LDAP searches remain available. **Run request**
is enabled after a model is selected and approved, a directory is connected, a request is entered,
and active operations have finished. A search is resolved to a typed LDAP search and follows the
AI confirmation preference (on by default). A mutation is resolved to a typed dry-run preview;
the request field applies it only after explicit confirmation, regardless of search preferences.
The separate Directory Workbench controls retain their own confirmation flows. Press **Enter** to run the request;
**Shift+Enter** inserts a new line. No font picker is provided; the application keeps the system
font.

Ambiguous or incomplete requests return a clarification instead of choosing a target or
attribute. The model may misunderstand a request; review every search proposal and dry-run
preview. Do not include passwords or other credentials in natural-language requests. Use the
explicit credential form when a password is required.

**Help → Data Lens help** lists supported requests, example phrases, effective built-in aliases,
backend differences, and safety constraints in copyable text. Examples illustrate intent and
are not a guarantee of how a particular GGUF model interprets a request. Directory schema and
permissions still determine which attributes and operations are available.

Data Lens accepts these target session commands, with or without a connection. They are matched
locally against the whole request and never use the model, so no model needs to be selected:

| Command | Result |
| --- | --- |
| `connect to <profile>` or `switch to <profile>` | Connects to a saved profile. An existing session is disconnected and its data cleared first. `connect` alone uses the selected or last connected profile. |
| `disconnect` | Ends the current session, like the **Disconnect** button. |
| `list targets` | Lists saved profiles and marks the connected and last connected ones. |
| `current target` | Shows the connected profile, its base DN, and whether it is read-only. |

A profile is matched by name, then host, then a unique part of its name; an ambiguous or unknown
name is reported without changing the session. One target is connected at a time. Unsaved
attribute changes and active operations still prompt before a session ends, and a missing bind
password is still requested. Results appear below the request and in the **Data Lens log**.
Searches and mutation previews stay unavailable until a connection has succeeded.

Choose a local GGUF model with **Choose GGUF file** in application settings (or
**Settings → Data Lens → Choose local GGUF model**). DataSifter asks you to
confirm that you independently verified its approved source and record that source. It stores the
model's SHA-256 digest and checks the file against it before inference. This is a local integrity
check and operator attestation, not publisher authentication; obtain models from an organization-
approved source and verify them independently. Inference runs locally, uses structural directory
schema rather than entry values, and requests one JSON intent conforming to a strict Pydantic
schema. Malformed JSON or schema violations trigger one bounded repair attempt; a second failure
is reported instead of being treated as an intent. Only relevant object-class and attribute names
(for example "phone" → `telephoneNumber`, `homePhone`, `mobile`) are sent, trimmed to fit the
model's 4,096-token context. The directory resolver maps everyday terms, resolves names only when
unambiguous, validates LDAP filters, and asks for clarification when information is missing.
Search review shows the filter, base DN, scope, returned attributes, and result limit. Search
results redact credential attributes. Mutation proposals use the typed resolver and display a
read-only dry-run preview; the model has no call path to an LDAP write.

The **Data Lens log** tab records, for this session only, model changes (previous/new file
and approved SHA-256), enable/disable events, and the intent category processed. It does not
retain the request, model prompt, or raw model response. **Clear** empties the log.

**Download / select models** in application settings (also available under
**Settings → Data Lens**) opens a curated model manager. Each entry shows a
short description, and the drop-down tooltip repeats it. The list below reflects the
**eight models in the application catalog as of 2026-11-02**, not a list of all compatible
GGUF models. All catalog files are pinned to an exact
revision, size, and SHA-256:

| Model | Publisher / license | GGUF quantized by | Size |
| --- | --- | --- | --- |
| [SmolLM2 1.7B Instruct Q4_K_M](https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct) | Hugging Face, Apache-2.0 | Hugging Face | 0.98 GiB |
| [Google Gemma 3 1B Instruct Q4_K_M](https://huggingface.co/google/gemma-3-1b-it) | Google, Gemma Terms of Use | ggml-org | 0.75 GiB |
| [Google Gemma 3 4B Instruct Q4_K_M](https://huggingface.co/google/gemma-3-4b-it) | Google, Gemma Terms of Use | ggml-org | 2.32 GiB |
| [Meta Llama 3.2 1B Instruct Q4_K_M](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct) | Meta, Llama 3.2 Community License | hugging-quants | 0.75 GiB |
| [Meta Llama 3.2 3B Instruct Q4_K_M](https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct) | Meta, Llama 3.2 Community License | hugging-quants | 1.88 GiB |
| [Meta Llama 3.1 8B Instruct Q4_0](https://huggingface.co/meta-llama/Meta-Llama-3.1-8B-Instruct) | Meta, Llama 3.1 Community License | ggml-org | 5.62 GiB |
| [Phi-3 Mini 4K Instruct Q4](https://huggingface.co/microsoft/Phi-3-mini-4k-instruct) | Microsoft, MIT | Microsoft | 2.23 GiB |
| [Microsoft Phi-4 14B Q4_K_S](https://huggingface.co/microsoft/phi-4) | Microsoft, MIT | Microsoft | 7.86 GiB |

### Choosing a model

- **Lower memory use:** try SmolLM2 1.7B or a 1B model for simple requests.
- **Middle ground:** try Gemma 3 4B, Llama 3.2 3B, or Phi-3 Mini.
- **More capacity:** try Llama 3.1 8B or Phi-4 14B if memory and response time permit.
  The catalog estimates roughly 8 GB and 10 GB of free RAM respectively; actual requirements
  depend on the runtime, context, and hardware. Download size is not total RAM usage.

These are starting points, not measured LDAP-accuracy rankings. Real-model evaluation is
still on the roadmap. Data Lens currently uses a **4,096-token context** regardless
of a model's advertised maximum. Simple requests may bypass inference through the parser;
a larger model will not fix a deterministic parser bug. Keep filter review enabled.
Other compatible GGUF files can be selected locally, subject to the same approval and
integrity checks. Models are not trained or fine-tuned by the application.

The [source catalog](./src/datasifter/ai/model_catalog.py) is the source of truth for
filenames, revisions, sizes, hashes, publisher information, and download repositories.
Catalog inclusion is not a guarantee of inference compatibility or quality.

Google's own GGUF repositories require a Hugging Face login, and Meta does not publish GGUF files.
For those models, the catalog uses ungated quantizations from the llama.cpp maintainers (ggml-org)
and Hugging Face's quantization team (hugging-quants). The manager shows publisher, license,
quantizer, size, and SHA-256, with clickable links to the upstream model card/license and to the
pinned download repository. Review the model card and license before explicitly downloading.
Downloads contact Hugging Face and its HTTPS file hosts, not an inference service; no LDAP data
or credentials are sent. No download or catalog network request occurs merely by opening it.
Choose a destination folder, use **Cancel download** to abort (a blocked read has a 10-second
timeout), or select an existing local GGUF file. Downloads verify the GGUF header, exact size,
and pinned publisher SHA-256 before publishing the file; failed/canceled partial files are removed.
Existing files are never overwritten. After downloading or selecting a model, the existing
source-attestation and integrity-check flow is still required before inference.
**Load model into memory after download or selection** (checked by default) loads the approved
model right after attestation, so the first AI request starts immediately; the AI panel then
reports "Loaded in memory and ready." Uncheck it to load the model on first use instead. The
loaded model is reused for later requests and released when a different model is approved.
Model selection and source approval are saved independently of the settings screen's Save/Cancel
buttons. Other settings are applied together only on Save; Cancel leaves them unchanged.
Saving settings is disabled while operations are active.

## Local data and credentials

Non-sensitive settings are stored under `%APPDATA%\\DataSifter` on Windows and
`~/.config/datasifter` on macOS/Linux. Connection passwords are stored through the operating
system keyring and are not written to profile JSON. Optional private-key passphrases are also
kept in the OS keyring. Profiles support anonymous, simple bind, and TLS-protected SASL EXTERNAL.

Use **Add profile** to open the connection wizard: enter connection and authentication details,
optionally configure TLS and run connection checks, then choose a Base DN and save. Use **Back**
to revise an earlier step. Select a saved profile and choose **Edit profile** to edit it in one dialog.
DataSifter selects the last successfully connected profile when it starts and uses it if you click
**Connect** with no profile selected. It does not reconnect automatically.
New profiles default to **Simple bind**; choose another authentication mode when needed. **List
profiles** displays saved profile names, endpoints, transports, and authentication modes, and is
disabled when no profiles are saved.
Use **Delete profile** to remove the selected local profile. After confirmation, DataSifter
deletes that profile's saved password and client-key passphrase from the OS keyring, its
session-only bind password, and its profile-scoped TOFU certificate; it does not delete
directory entries or user-supplied certificate/key files. Deleting the active profile first
disconnects and requires resolving unsaved entry changes. If local cleanup fails, the profile
remains saved and an error is shown; some associated local data may already have been removed.
New profiles default to **Plain LDAP** on port 389; bind credentials are unencrypted in this
mode, so use it only on a trusted network. Saved profiles retain their configured transport.
Select **Simple bind** to enable the **Bind password** field. **Show password** reveals it locally;
**Save password in OS keyring** controls whether it persists securely in the operating-system
keyring. If not saved, a password entered for a new profile is held in memory for the current
session; after restarting, Connect prompts for it. New profiles do not save passwords unless the
keyring option is selected. Clearing a saved password removes it from the keyring. Saving edits to
the active profile requires connecting again.
The Show password and Save password options share one row in both flows.

The connection wizard and **Edit profile** dialog include two independent diagnostics using
the current field values, including the entered bind password. No profile or credentials are
saved by testing. A profile name and base DN are not needed for these tests; they are required
when saving. Inputs and Save are disabled during a test; Cancel requests cancellation.
**Cancel check** stops a running network or authentication check without closing the dialog.
Tests are optional and do not gate saving. Changing settings clears the displayed test result.
In the wizard, both checks are on the security and connection checks step; the edit dialog
places them near the corresponding fields. After authentication succeeds, the application reads
Root DSE naming contexts and populates the editable **Base DN** dropdown, preferring the server's
default context when advertised. The Root DSE query requests its available user and operational
attributes so server-specific context attributes are not rejected by local schema validation.
Existing/manual selections are
preserved. If the server advertises no contexts, enter a DN manually; discovery errors are
reported as test failures. Changing connection or credential settings clears discovered options.

For a first-use or changed TLS certificate, verify and accept its fingerprint,
then click **Test authentication** again to perform the bind. Accepted draft trust is kept
temporary until Save; canceling the dialog does not replace the saved profile's trust.

## Directory Workbench

The selected entry's distinguished name appears read-only above its attributes; select the
text or click **Copy DN** to copy it.
Toggling **Show operational attributes** re-reads the selected entry from the server so
server-maintained attributes (for example `entryUUID`, `modifyTimestamp`, `modifiersName`,
`subschemaSubentry`, `ds-entry-checksum`) appear or disappear immediately; they are read-only.
Operational attributes are only returned to authenticated binds with sufficient access.
Click the **Attribute** or **Values** column header to sort entries' attributes; click again
to reverse the order. Multivalued attributes display one value per line with automatically
sized rows. Their multiline editor preserves every value (one value per line); press **Enter**
to commit the edit or **Shift+Enter** to add another value line. **Tab** also commits and opens
the next editable value (**Shift+Tab** the previous one), skipping read-only attributes. Read-only
and binary attributes are excluded from submitted changes.
After a search, attributes named in the filter (for example `telephoneNumber` and `mobile` for
`(|(telephoneNumber=*732*)(mobile=*732*))`) are shown bold on a highlighted background with the
tooltip "Used in the current search filter". The generic `(objectClass=*)` term and DN-component
matches such as `(ou:dn:=People)` are not highlighted. Browsing after connecting clears the
highlight. Turn it off or on in **Application settings → Appearance** (or
**Settings → Appearance → Highlight searched attributes**); the choice is
saved with the local configuration.
**Add attribute** offers a typed, case-insensitive substring search through the directory's
schema attributes; select a matching attribute and enter its initial value.
**Save attribute changes** is disabled until editable values differ from the loaded entry,
is shown in bold while changes are pending, and dims again when edits are reverted.
Selecting another entry, searching, reconnecting, moving, editing the active profile, or closing
the application with pending changes asks whether to discard them (Cancel keeps the entry).
A search that matches nothing shows a **No entries found** dialog and leaves the current
directory view unchanged.
Delete and rename/move actions require confirmation by default. Their **Don't ask again**
options are saved separately in the configuration. **Application settings → Safety** and the
**Settings → Directory safety** shortcuts restore or override each confirmation.
With delete confirmation disabled, deletion is non-recursive; recursive deletion remains an
explicit option in the enabled delete confirmation dialog.

The filter editor highlights LDAP attribute names, operators, and boolean operators.
**Clear filter** resets it to `(objectClass=*)` and searches again; pending entry edits
still require confirmation, and a failed search leaves the current directory view unchanged. Use
**Schema inspector** and **Load directory schema** after connecting to display the server's
object classes, required/optional attributes, and advertised attribute types.
Select an **objectClass** to restrict the list to that class and its allowed attributes,
including attributes inherited from parent classes. Type in the attribute search field to
progressively filter attribute names (case-insensitive substring matching); clear it and choose
**All object classes** to restore the complete listing. Changing directories clears the old schema.
Operational attributes in the entry grid are italicized in both columns with a read-only tooltip;
their existing edit restrictions and search highlighting are preserved.

The **Protocol log** shows sanitized decoded ldap3 outbound request dictionaries when available,
with safe operation summaries as a fallback. Bind credentials, password-modify values, LDAP
add/modify attribute values, controls, and LDAP search assertion values are redacted. Search filter
structure and DNs remain visible for diagnosis. Logs are displayed locally in the application
and persisted when launched through `python -m datasifter`, the `datasifter` command, or the
standalone application.

### LDIF import and export

Use **Directory → Import LDIF change records** to validate and preview an LDIF file before
applying it. Content records and `add`, `modify`, `delete`, `modrdn`, and `moddn` change
records are applied in file order after explicit confirmation. Validation covers LDIF v1
record syntax, folded lines, base64 values, distinguished names, and supported change
operations; add records must include a non-empty `objectClass`. Files containing malformed
records are rejected before any directory changes are sent. The profile's read-only guard
also blocks imports.

LDAP does not make an arbitrary multi-record import atomic. If the server rejects a record
or the operation is canceled, earlier records or earlier sub-operations in a modify record
may already have succeeded; the error reports the record position and completed-record count.
After a completed or failed import, DataSifter attempts to refresh the directory that received
the changes, not a different profile selected in the toolbar.
LDIF controls, URL-valued attributes,
and change types other than the listed operations are rejected rather than ignored. Format
validation cannot predict whether the connected server's schema or access controls will
accept each change.

Use **Directory → Export selected entry as LDIF** to export only the highlighted entry,
not its descendants. Export reads the current server values, excludes operational
attributes, validates the generated LDIF, and writes it atomically. The confirmation warns
that returned attributes may contain sensitive directory data; protect the exported file.

### Persistent diagnostics

On Windows, the log is `%APPDATA%\DataSifter\logs\datasifter.log`. On other platforms it is
`~/.config/datasifter/logs/datasifter.log`. Logs append across runs and rotate at 5 MiB,
retaining three backups (`datasifter.log.1` through `.3`). No logs are stored in the repository.
If the log cannot be created, startup reports the error rather than running without diagnostics.

The file records sanitized LDAP protocol events, application start/stop, worker failures,
uncaught Python exceptions (including Qt callback exceptions), and NLP intent-generation
outcomes. Exception diagnostics contain exception types and file/function/line locations,
including chained exceptions, but omit exception messages, source lines, and local variables
because these can contain passwords. Raw NLP requests, model prompts, generated filters, and
model responses are **not persisted**; the in-app Data Lens log records only the intent category.
Do not enter passwords into the request field. Clearing an in-app log does not delete log files.

These are diagnostic logs, not a complete audit trail or native-crash dump. DNs and directory
structure remain visible, so treat log files as sensitive and review them before sharing.
An unexpected Python error displays the diagnostic path; more detailed exception messages may
still appear in the terminal.

Data Lens confirmed mutations additionally append structured events to
`logs/datalens-audit.jsonl` under the profile configuration directory. Events record a timestamp,
correlation ID, operation, backend and outcome; they omit DNs, attribute values, prompts,
credentials and exception messages. This file is not rotated automatically. It covers only
Data Lens mutation proposals, not other application or directory activity, and is not tamper-proof.
An audit write failure blocks execution before submission; if an outcome cannot be recorded
after submission, DataSifter reports that the directory must be checked before retrying.

Canceling active operations closes their LDAP sockets and signals every current worker. Cancellation
is passed to network tests, certificate inspection, authentication, schema reads, entry reads, and
directory writes. A canceled write may already have reached the server; DataSifter reports this
uncertainty and advises verifying the directory before retrying. Network connection establishment is
bounded by the connection timeout.

The schema inspector displays object classes and their required/optional attributes, plus LDAP
attribute OIDs, syntax identifiers, descriptions, matching rules, and single-value constraints when
advertised by the directory. The LDAP filter editor highlights attribute names, operators, and
boolean operators.

## Data Lens engine

`datasifter.datalens` is the typed directory layer called by the Data Lens request UI and,
later, an API or MCP server. It has no user-interface dependencies. The Data Lens-specific
phases 1 (typed operations), 2 (aliases and clarification), 3 (local GGUF intents), 4
(confirmation, audit and service boundary), and 5 (help and remediation) are implemented.
Mutation previews are rechecked
immediately before execution. LDAP does not make the preview and write atomic; concurrent
changes may still occur, and recursive deletion may partially complete if a later delete fails.

| Module | Contents |
| --- | --- |
| `datalens.models` | Pydantic models: `SearchSpec`, `SearchResult`, `DirectoryEntry`, `AttributeChange`, the create/update/delete/move plans, `DryRunResult`, `MutationResult`, `ClarificationPrompt` |
| `datalens.adapters` | `StandardLdapAdapter`, `ActiveDirectoryAdapter`, `create_adapter`, `detect_backend` |
| `datalens.aliases` | `AliasRegistry`: everyday phrases mapped to concepts, and each concept to its candidate attributes on Standard LDAP and on Active Directory |
| `datalens.resolver` | `DirectoryResolver`: terms to attributes, names to entries, and requests in everyday terms to a search spec or a mutation plan, or to clarification questions |
| `datalens.intents` | Strict Pydantic schemas for `search`, `proposed_mutation`, and `clarify`; preparation routes validated intents through `DirectoryResolver` and never applies mutations |
| `datalens.service` | UI-independent confirmed mutation execution and metadata-only local audit events |
| `datalens.help` | Versioned structured catalog of intents, examples, aliases, backend differences and safety constraints |
| `datalens.schema` | `DirectorySchema`: the server's object classes and attribute types |
| `datalens.dn` | DN and RDN parsing, comparison and parent/descendant checks |
| `datalens.errors` | `ReadOnlyDirectoryError`, `MutationRejectedError`, `ConfirmationRequiredError` |

The adapters reuse `LDAPService` for transport, so TLS validation, certificate pinning,
cancellation, paging and the redacted protocol log are unchanged.

`get_help_catalog()` returns a Pydantic catalog that callers can serialize with
`model_dump(mode="json")` or inspect with `HelpCatalog.model_json_schema()`. Pass an
`AliasRegistry` to include site-specific aliases. This is an in-process Python API, not an
HTTP or MCP server. `datasifter.remediation.translate_error()` maps typed LDAP results,
directory preview issues and model failures to structured categories, codes and next steps.
Unknown errors retain their message without guessing a cause. The UI displays actionable
diagnostics in copyable text; directory server logs may contain additional detail.

```python
from datasifter.datalens import DirectoryBackend, SearchSpec, create_adapter
from datasifter.directory.service import LDAPService

adapter = create_adapter(LDAPService(profile, password), DirectoryBackend.STANDARD_LDAP)
people = adapter.search_entries(SearchSpec(filter=adapter.user_filter("alexsmith"), size_limit=50))

dn = "cn=alexsmith,ou=People,dc=example,dc=com"
preview = adapter.delete_entry(dn)                      # dry run: nothing is written
if preview.preview.can_apply:
    adapter.delete_entry(dn, dry_run=False, confirmed=True)
```

- **Backend choice:** pass `DirectoryBackend.STANDARD_LDAP` or `ACTIVE_DIRECTORY`.
  `detect_backend(service)` reads the RootDSE when the kind of server is not known.
- **Search:** `search_entries` pages through results and stops at `size_limit`; `truncated`
  reports whether more entries matched.
- **Changes:** `create_entry`, `update_entry`, `delete_entry` and `move_entry` return a
  preview by default. The preview uses read-only lookups to check that the target and its
  parent exist, that the destination is free, and that the server schema knows the object
  classes and attributes and has its required attributes. A change is sent only with
  `dry_run=False` and `confirmed=True`, and only when the preview has no error.
- **Safety limits:** changes must stay inside the profile's base DN. The base DN and the
  account the profile signs in with cannot be deleted. A subtree is deleted only with
  `recursive=True`, and the preview lists every DN first. A read-only profile, or an adapter
  created with `read_only=True`, refuses changes before opening a connection.
- **Secrets:** results and errors show password attributes as `<redacted>`.
- **Active Directory:** names users, groups and containers by `CN`; refuses writes to
  `memberOf` and other system attributes; decodes `userAccountControl`; warns that an account
  created without a password is disabled; writes `unicodePwd` in the required encoding and
  only over LDAPS or StartTLS; protects critical system objects.
- **Limits:** a preview is advisory; access control or a concurrent change can still make
  the server refuse. Each lookup opens its own connection. The application imports the typed
  engine, but standalone builds have not yet been rebuilt or clean-target validated.

### Everyday terms and clarification

`DirectoryResolver` accepts requests the way people phrase them and never guesses. Each step
returns a definite result or a `ClarificationPrompt` with a reason, a question and, where
possible, the choices.

```python
from datasifter.datalens import DirectoryResolver, EntryKind

resolver = DirectoryResolver(adapter)
prepared = resolver.prepare_update(
    "Alex Smith",
    [{"term": "job title", "values": "Engineer"}, {"term": "reports to", "values": "Jane Doe"}],
    kind=EntryKind.USER,
)
if prepared.ready:                     # nothing to ask, and the preview found no error
    adapter.execute(prepared.plan, dry_run=False, confirmed=True)
else:
    for prompt in prepared.clarifications:
        print(prompt.reason, prompt.question, [option.label for option in prompt.options])
```

- **Attribute terms:** "email", "job title", "reports to", "username" and about forty other
  concepts map to the attribute that holds them on the connected backend, for example
  `uid` on Standard LDAP but `sAMAccountName` or `userPrincipalName` on Active Directory,
  and `departmentNumber` or `ou` against `department`. Candidates the server's schema does
  not define are dropped. An exact attribute name is accepted in any letter case.
- **Broad terms:** "phone" covers the work, mobile and home numbers. A search matches any
  of them. A change asks which one is meant.
- **Names:** a user, group or organizational unit is found by DN or by exact name. One match
  resolves. Several matches, and near matches when nothing matches exactly, are listed as
  choices and never picked automatically. Values of attributes that point at other entries
  (`manager`, group members) are resolved the same way.
- **Missing information:** creating an entry asks for its name, its location and the
  attributes the schema requires; a move asks for a destination; deleting an entry that has
  subordinate entries asks whether to include them.
- **Account state:** "disabled", "active" and "locked" use `userAccountControl` and
  `lockoutTime` on Active Directory, and whichever of `nsAccountLock`,
  `ds-pwp-account-disabled`, `loginDisabled` and `pwdAccountLockedTime` the server's schema
  defines on Standard LDAP. When there is none, the engine says so.
- **Site-specific terms:** `AliasRegistry.register(AttributeConcept(...))` adds a concept.
  A phrase that already belongs to another concept is refused unless `replace=True`.
- **Plans and secrets:** `prepared.plan` is what `adapter.execute` runs and can hold a
  password, so it is excluded from `repr` and from serialized output; `prepared.preview`
  is the display-safe view.
- **Existing alias tables:** the Data Lens request field still uses the tables in
  `directory/filters.py`. A test keeps the registry in agreement with them.

## LDAP transport

Profiles support plain LDAP, StartTLS, and LDAPS. TLS connections require certificate validation.
Configure a CA certificate for private PKI. Never use plain LDAP on an untrusted network.

## Tests

```powershell
python -m pytest
```

The automated tests do not require a live LDAP server or model. They do not replace deployment
validation against the directory products and certificate configurations used in your environment.
The Data Lens adapters are tested against ldap3's in-memory mock directory with offline OpenLDAP
and Active Directory schemas.
Optional live tests in `tests/test_live_ldap.py` and `tests/test_live_datalens.py` run against
non-production directories when local targets are configured, and are skipped otherwise; see
[CONTRIBUTING.md](./CONTRIBUTING.md#live-ldap-tests).
Build and validate the standalone application separately on each target OS; the PyInstaller
configuration and native dependencies are platform-specific.

## Standalone build

Install the packaging extra and run the PyInstaller spec from the project root:

```powershell
python -m pip install -e ".[packaging,bom]"
python -m PyInstaller datasifter.spec
```

Each invocation of the spec reserves the next build version, increasing it by `0.001`.
The version appears in the window title and permanently in the status bar. For testing, the
title also shows when the build was made in local time, for example
`DataSifter 2.0 - v0.006 (built 2026-11-01 13:06:55)`. The spec records the time in the
generated `src/datasifter/_build_stamp.py`. Running from source (`python -m datasifter`) shows
`source <time>` instead, using the newest modification time of the package's Python files.
Package metadata
reads the same source version. A failed build still consumes its reserved version; run only
one build at a time.

To explicitly override the version for a build (including rebuilding the same version):

```powershell
$env:DATASIFTER_BUILD_VERSION = "0.001"
python -m PyInstaller datasifter.spec
Remove-Item Env:\DATASIFTER_BUILD_VERSION
```

The spec produces a directory build under `dist`. Validate it on each target operating system
because PyQt6, OpenSSL, keyring backends, and optional llama.cpp native libraries are platform-specific.
On Python installations providing an `abi3t-compat\python3t.dll` compatibility shim, the spec
includes it for native extension loading. Keep the entire application directory together;
do not copy only the executable.

## Library and component inventory

Consult [BOM.md](./BOM.md) and the CycloneDX 1.6 [bom.json](./bom.json) before adding functionality.
Reuse existing components where appropriate, then refresh both inventories after changes:

```powershell
python -m pip install -e ".[dev,local-ai,packaging,bom]"
python tools\generate_bom.py
```

Generation validates the CycloneDX schema and dependency references, resolves only the project's
installed dependency closure, and inventories source files and available packaged native binaries.
It fails explicitly if a required distribution is missing or incompatible. Standalone builds refresh
both files after packaging. Platform-specific dependencies for other operating systems and unprovided
LDAP servers/models are not claimed as resolved. Regenerate on each target build environment.
