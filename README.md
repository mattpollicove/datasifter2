# DataSifter 2.0

**ALPHA CODE / WORK IN PROGRESS**

Experimental, actively developed software; not production-ready. Features, configuration
formats, and behavior may change. Test against a non-production directory and keep backups
before trying write operations.

Original code is [MIT-licensed](./LICENSE). Dependencies and models have separate licenses;
in particular, the installed PyQt6 distribution is GPL-3.0-only. See
[third-party licensing obligations](./THIRD_PARTY_NOTICES.md) before distributing the app.

DataSifter is a local-first LDAP desktop client. This implementation targets Phase 1 of
[`datasifter2.0_spec.md`](./datasifter2.0_spec.md): LDAP directory workspaces and an optional
offline llama.cpp assistant.

Directory Lens supports **local GGUF model files only**, loaded through
`llama-cpp-python`. Selection and loading reject other extensions and files without a GGUF
header, even if renamed to `.gguf`. A GGUF header alone does not establish model compatibility
or authenticity; llama.cpp must support the model architecture, and source approval and
SHA-256 verification still apply. Ollama, remote inference services, and other model formats
are not supported.

## Current progress

Status as of **2026-11-02**: **Phase 1 LDAP functionality is implemented and under
active alpha testing**. This is not a production-readiness or interoperability certification.
The [implementation plan](./plan.md) records the original Phase 1 work; the
[specification](./datasifter2.0_spec.md) describes the broader vision, not a list of
features all available today.

### What's working in the current implementation

| Area | Implemented capabilities |
| --- | --- |
| Profiles and credentials | Create/edit profiles, network and authentication checks, local JSON preferences, OS keyring integration, read-only profiles |
| Directory workspace | Paged DIT browsing, LDAP filter searches, attribute editing, schema-guided entry creation, delete, rename/move, password modification |
| Transport and safety | Plain LDAP warnings, StartTLS/LDAPS, custom CA/client certificates, TOFU approval and rotation checks, write confirmations |
| Schema inspector | Object-class filtering with inherited attributes and progressive attribute-name search |
| Directory Lens | Local GGUF selection/download/approval, integrity checks, model reuse, streamed filter generation, deterministic simple searches, filter review, model-change logging |
| Usability | Grouped settings, persistent AI enable/disable, Enter-to-generate, operational-attribute italics, search highlighting, cancellation and sanitized protocol logs |
| Packaging | Windows standalone build workflow, version increments, generated bill of materials |

These features have offline automated coverage; availability in the implementation does
not mean every operation has been verified against a live server or every listed model.

### Testing evidence and current limitations

- Most recent source validation: **219 tests passed** on Windows on 2026-11-02,
  plus Ruff checks for the GGUF-only change and a Python syntax check for its inference adapter.
- Automated coverage includes configuration, LDAP service behavior, filters, certificates,
  workers, Qt UI interactions, model approval/download safeguards, and BOM generation.
  Model inference tests use fakes where appropriate; passing them is not a real-model benchmark.
- Live LDAP-server interoperability, end-to-end TLS/client-certificate behavior on clean
  machines, and real-GGUF accuracy/performance still require environment-specific testing.
- The previous Windows standalone build predates recent source changes. The current source
  has not been repackaged or revalidated as a standalone release; macOS/Linux builds are
  not certified.
- Natural-language interpretation can be wrong. Review generated filters; complex requests
  may require rephrasing or a manually written LDAP filter.

## Roadmap

The ordering below describes direction, not release dates or a guarantee of delivery.

| Stage | Status | Next work |
| --- | --- | --- |
| Phase 1: LDAP alpha stabilization | Implemented; validation and refinement ongoing | Live LDAP/TLS interoperability matrix, real-model accuracy/performance evaluation, parser regression cases, usability/accessibility refinements, fresh standalone builds and clean-target checks |
| Phase 2: RDBMS expansion | Planned; not implemented | Database drivers and explorer, editable data grids, SQL console, refresh behavior, local SQL assistance |
| Phase 3: Synchronization and provisioning | Planned; not implemented | LDAP/RDBMS mappings, delta calculation and dry-run audit, Okta and Microsoft Entra ID connectors |

AI support remains local GGUF through llama.cpp. Remote inference, Ollama integration,
automatic model training, and a font picker are not current features.

## Contributions welcome

Bug reports, reproducible parser examples, documentation fixes, and code contributions
are welcome. **Every contribution must include a test plan, evidence of testing, and
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

The llama.cpp package may need a platform-specific build toolchain. To run without AI support,
install `-e ".[dev]"`; AI actions will report that the optional dependency or model is missing.
**Directory Lens** is the local AI filter assistant. Open **Settings → Application settings**
for grouped **Directory Lens**, **Safety**, and **Appearance** options. Disable Directory Lens
to hide its workspace panel and release the loaded model; re-enable it from the same screen.
The approved model file is retained, and manual LDAP searches remain available. **Generate local
filter** is disabled until a model is selected and approved, a directory is connected, a request
is entered, and active operations have finished. Press **Enter** to generate; **Shift+Enter**
inserts a new line. No font picker is provided; the application keeps the system font.

Choose a local GGUF model with **Choose GGUF file** in application settings (or
**Settings → Directory Lens → Choose local GGUF model**). DataSifter asks you to
confirm that you independently verified its approved source and record that source. It stores the
model's SHA-256 digest and checks the file against it before inference. This is a local integrity
check and operator attestation, not publisher authentication; obtain models from an organization-
approved US, Japanese, or European source and verify them independently. Inference runs locally,
streams generated text in the workspace,
uses structural directory schema rather than entry values, and validates the completed filter before
review or execution. The server's schema names are read once per connection; only core attributes
and names related to the request (for example "phone" → `telephoneNumber`, `homePhone`, `mobile`)
are sent, trimmed to fit the model's 4,096-token context. Simple requests such as
"show fax, phone or mobile numbers with 732" are built directly, without the model, as
`(|(facsimileTelephoneNumber=*732*)(telephoneNumber=*732*)(mobile=*732*))`; "starting with" and
"ending with" become prefix and suffix matches. "In state PA" or "city = Erie" become equality
matches (`(st=PA)`, `(l=Erie)`). Everyday words are mapped to schema attributes both in the
prompt and in model output: state/province → `st`, city/town/location → `l`, zip/postcode →
`postalCode`, country → `c`, company/organization → `o`, phone, fax, mobile, email, and similar;
filters naming attributes absent from the schema are rejected with a message to rephrase.
Search phrasing such as "lastname starting with the letter b" or "last name begins with b"
becomes `(sn=b*)`; "first name" is also supported. "The letters", "the word", and "the digits"
can introduce a search value, and "ends in" is a suffix match. Quote multiword values, for
example `cn contains "Jane Doe"` becomes `(cn=*Jane Doe*)`. Quoted text is treated literally
and LDAP-special characters are escaped. Unsupported extra conditions, incomplete phrases,
or unknown attributes fall back to the model rather than being silently dropped.
The **Directory Lens log** tab records, for this session only, model changes (previous/new file
and approved SHA-256), enable/disable events, each request, and the exact prompt sent to the
model (request text plus schema attribute names, never entry values), the model output, and the
resulting filter or error; requests built without the model are noted as such. **Clear** empties
the log. "Contains/including X" becomes a
substring match (`*X*`), and a location such as "in ou People" becomes an RFC 4515 DN-component
match (`(ou:dn:=People)`), which is added automatically if the model omits it.

**Download / select models** in application settings (also available under
**Settings → Directory Lens**) opens a curated model manager. Each entry shows a
short description, and the drop-down tooltip repeats it. All files are pinned to an exact
revision, size, and SHA-256:

| Model | Publisher / license | GGUF quantized by | Size |
| --- | --- | --- | --- |
| SmolLM2 1.7B Instruct Q4_K_M | Hugging Face, Apache-2.0 | Hugging Face | 0.98 GiB |
| Gemma 3 1B Instruct Q4_K_M | Google, Gemma Terms of Use | ggml-org | 0.75 GiB |
| Gemma 3 4B Instruct Q4_K_M | Google, Gemma Terms of Use | ggml-org | 2.32 GiB |
| Llama 3.2 1B Instruct Q4_K_M | Meta, Llama 3.2 Community License | hugging-quants | 0.75 GiB |
| Llama 3.2 3B Instruct Q4_K_M | Meta, Llama 3.2 Community License | hugging-quants | 1.88 GiB |
| Llama 3.1 8B Instruct Q4_0 | Meta, Llama 3.1 Community License | ggml-org | 5.62 GiB |
| Phi-3 Mini 4K Instruct Q4 | Microsoft, MIT | Microsoft | 2.23 GiB |
| Phi-4 14B Q4_K_S | Microsoft, MIT | Microsoft | 7.86 GiB |

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

Use **Add profile** or select a saved profile and choose **Edit profile** to configure credentials.
New profiles default to **Simple bind**; choose another authentication mode when needed. **List
profiles** displays saved profile names, endpoints, transports, and authentication modes, and is
disabled when no profiles are saved.
New profiles default to **Plain LDAP** on port 389; bind credentials are unencrypted in this
mode, so use it only on a trusted network. Saved profiles retain their configured transport.
Select **Simple bind** to enable the **Bind password** field. **Show password** reveals it locally;
**Save password in OS keyring** controls whether it persists securely in the operating-system
keyring. If not saved, a password entered for a new profile is held in memory for the current
session; after restarting, Connect prompts for it. New profiles do not save passwords unless the
keyring option is selected. Clearing a saved password removes it from the keyring. Saving edits to
the active profile requires connecting again.
The Show password and Save password options share one row in the profile dialog.

The **Add profile** and **Edit profile** dialogs include two independent diagnostics using
the current field values, including the entered bind password. No profile or credentials are
saved by testing. A profile name and base DN are not needed for these tests; they are required
when saving. Inputs and Save are disabled during a test; Cancel requests cancellation.
**Cancel check** stops a running network or authentication check without closing the dialog.
Tests are optional and do not gate saving. Changing settings clears the displayed test result.
**Test connection** sits directly below Host/IP and Port. **Test authentication** sits directly
below Bind DN and Bind password. After authentication succeeds, the dialog reads Root DSE
Root DSE naming contexts and populates the editable **Base DN** dropdown, preferring the server's
default context when advertised. The Root DSE query requests its available user and operational
attributes so server-specific context attributes are not rejected by local schema validation.
Existing/manual selections are
preserved. If the server advertises no contexts, enter a DN manually; discovery errors are
reported as test failures. Changing connection or credential settings clears discovered options.

For a first-use or changed TLS certificate in the dialog, verify and accept its fingerprint,
then click **Test authentication** again to perform the bind. Accepted draft trust is kept
temporary until Save; canceling the dialog does not replace the saved profile's trust.

## Directory workspace

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

The filter editor highlights LDAP attribute names, operators, and boolean operators. Use
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
structure and DNs remain visible for diagnosis. Logs are displayed locally in the application.

Canceling active operations closes their LDAP sockets and signals every current worker. Cancellation
is passed to network tests, certificate inspection, authentication, schema reads, entry reads, and
directory writes. A canceled write may already have reached the server; DataSifter reports this
uncertainty and advises verifying the directory before retrying. Network connection establishment is
bounded by the connection timeout.

The schema inspector displays object classes and their required/optional attributes, plus LDAP
attribute OIDs, syntax identifiers, descriptions, matching rules, and single-value constraints when
advertised by the directory. The LDAP filter editor highlights attribute names, operators, and
boolean operators.

## LDAP transport

Profiles support plain LDAP, StartTLS, and LDAPS. TLS connections require certificate validation.
Configure a CA certificate for private PKI. Never use plain LDAP on an untrusted network.

## Tests

```powershell
python -m pytest
```

The automated tests do not require a live LDAP server or model. They do not replace deployment
validation against the directory products and certificate configurations used in your environment.
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
