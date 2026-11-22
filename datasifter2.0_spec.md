# Project DataSifter 2.0: Master Technical Specification

This specification includes current Phase 1 functionality and future design targets.
The product-roadmap Phase 2 and Phase 3 capabilities are planned, not implemented; these
roadmap stages are separate from Data Lens's internal delivery phases. See the
[README](./README.md#testing-evidence-and-current-limitations) for validation evidence
and known limitations; specification targets are not compatibility certifications.

## 1. System Overview & Scope
* **Project Name:** **DataSifter 2.0**
* **Target Operating Systems (not certified):** Windows 10/11, macOS 12+, Linux (X11 & Wayland on GNOME/KDE). Current source tests have run on Windows; current standalone builds and macOS/Linux builds require further validation.
* **Delivery Architecture:** **Native Thick Client Desktop Application** (compiled standalone binary via PyInstaller/Nuitka). Designed specifically for on-premises enterprise environments, direct local network/VPN socket connectivity, and zero intermediate cloud/server infrastructure.
* **Target Data Sources (not verified compatibility):**
  * **Directory Services (LDAP v3):** Active Directory, OpenLDAP, PingDirectory, FreeIPA, 389 Directory Server. Live interoperability for these products remains to be tested.
  * **Relational Databases (planned Phase 2):** Microsoft SQL Server, MySQL, MariaDB, Oracle Database, PostgreSQL.
* **Technology Stack:**
  * **GUI Framework:** Python 3.10+, **PyQt6** (Native GUI Framework).
  * **Directory Engine:** pure-Python `ldap3` library.
  * **Database Engines & Drivers (Phase 2):** `pyodbc` (SQL Server), `pymysql` (MySQL), `oracledb` (Oracle thin driver), `psycopg` (PostgreSQL), `SQLAlchemy` (core query abstraction & schema reflection).
  * **Secrets Vault:** `keyring` (OS-native secret storage).
* **Protocol Targets:** LDAP v3 (RFC 4511) and, in planned Phase 2, native RDBMS wire protocols. Full protocol compliance has not been certified.

---

## 2. System Architecture, Threading & Configuration

### 2.1 Multi-Engine Async Worker Model
* **Thread-Isolated Execution:** Directory operations, schema reflection, and local AI token streaming use background workers. SQL query execution is a Phase 2 target.
* **Connection Thread Safety:** 
  * **LDAP:** Isolated connection instances per worker task to guarantee socket thread safety.
  * **RDBMS (planned):** Dedicated background session pools to prevent socket interleaving across concurrent UI operations.
* **Task Cancellation & Progress:** Real-time progress indicators, status updates, and cancellation tokens to safely abort long-running tasks.

### 2.2 Secure Credential Storage & Local Configuration
* **Connection creation:** A guided wizard collects connection and authentication details, offers optional network and authentication checks and TLS configuration, and lets the user select or enter a discovered Base DN before saving. Existing profiles remain editable in a single dialog, with Save As creating a separately identified profile. Names must be unique ignoring case and surrounding whitespace. Saved credentials and profile-scoped TOFU trust are not copied.
* **OS-Native Keyring Integration:** Store connection passwords securely via the `keyring` Python library (Windows Credential Manager, macOS Keychain, Freedesktop Secret Service API / KWallet). Zero plain-text password storage on disk.
* **Local Configuration Store:** Non-sensitive profile metadata (connection endpoints, ports, SSL/TLS preferences, database names, TOFU certificate fingerprints, AI settings, confirmation preferences, and UI states) are stored in structured JSON or lightweight SQLite files within platform-specific user directories:
  * **Windows:** `%APPDATA%\DataSifter\`
  * **macOS / Linux:** `~/.config/datasifter/`

### 2.3 Advanced Security & Certificate Infrastructure
* **LDAP Transport Security:** Supports `Plain` (Port 389), `StartTLS` (Port 389), and `Implicit LDAPS` (Port 636) with strict hostname/Subject Alternative Name (SAN) matching (`ssl.CERT_REQUIRED`).
* **RDBMS TLS/SSL Support (planned Phase 2):** Native SSL mode configuration per database engine (e.g., `sslmode=require` for Postgres, Encrypted connection strings for SQL Server, Oracle Wallet/TCPS).
* **Client Certificate Support (mTLS):** Optional configuration for local client certificates (`.crt`/`.pem`), private keys (`.key`), and passphrases for environments utilizing certificate-based binding or SASL `EXTERNAL`.
* **Self-Signed Certificates & TOFU (Trust-on-First-Use):**
  * Self-signed and internal CA support is intended; environment-specific interoperability remains to be verified.
  * **Non-Production Warning:** The application interface explicitly displays a banner stating: **"Self-signed certificates are recommended for non-production environments only."**
  * **Interactive & Persistent Trust:** Unrecognized or self-signed certificates trigger an interactive modal displaying SHA-256 fingerprints, issuer details, subject, and expiration dates. Accepted fingerprints are stored locally within the profile configuration; subsequent connections validate against this stored fingerprint to instantly alert users of any certificate rotation or interception.

---

## 3. Data Source Workspaces & UI Layout

### 3.1 Dual-Mode Navigation Sidebar
The target design includes navigation for both data paradigms. The current Phase 1 UI provides LDAP navigation; the database view is planned:
1. **Directory Information Tree (DIT) View (LDAP Mode - Phase 1):** Hierarchical container tree with virtualized node chunking (loading child nodes in paginated batches of 500) to safely display high-density containers.
2. **Database Explorer View (RDBMS Mode - Phase 2):** Hierarchical Object Tree displaying Databases $\rightarrow$ Schemas $\rightarrow$ Tables / Views $\rightarrow$ Columns, Indexes, and Constraints.

### 3.2 Workspace & Tabbed Interface
The target multi-tab workspace includes the following; SQL and RDBMS capabilities are planned for Phase 2:
* **Attribute & Grid Editor:** Render directory entry attributes or table data rows with inline editing capabilities.
* **SQL Console / Filter Bar:** LDAP raw filter engine with syntax highlighting; a dialect-aware SQL execution editor is planned.
* **Schema Inspector:** Side-by-side view of directory object classes/syntaxes vs. RDBMS DDL schemas, primary/foreign keys, and data types.
* **Protocol & SQL Log Console:** LDAP request logging with execution timing metrics; SQL statement logging is planned.
* **Data Lens:** Interactive local assistant sidebar for filter drafting, explicit directory commands, and target session commands.

---

## 4. Data Object Scope & Operations (CRUD per Source)

### 4.1 LDAP Data Operations (Phase 1 Core)
* **Read / View:** Display attributes and values with a toggle for **Operational Attributes** (`createTimestamp`, `creatorsName`, `modifiersName`, `modifyTimestamp`, `entryUUID`).
* **Create & Update:** Add entries guided by structural/auxiliary schema object classes; modify attributes via Add, Replace, or Delete operations.
* **Delete:** Remove entries with confirmation dialogs and optional recursive subtree deletion protection.
* **ModRDN / Move:** Rename entry RDN or move across containers with `deleteOldRDN` support.
* **Password Management (RFC 3062):** Dedicated utility utilizing LDAP Password Modify Extended Operations for secure user password resets/changes.
* **LDIF:** Validate and preview LDIF v1 content and `add`, `modify`, `delete`, `modrdn`, and `moddn` change records before confirmed, ordered import. Add records require `objectClass`. Export and validate the selected entry as LDIF; descendant export is not included. Multi-record imports are not atomic, and unsupported controls and URL-valued attributes are rejected.

### 4.2 Database Objects & Update Rules (Phase 2 Core)
* **Database Level:** Create, drop, inspect, and alter database properties/collations.
* **Table Level:** Create, alter (add/modify/drop columns), truncate, and drop tables.
* **Row Level:** Direct DML operations (`INSERT`, `UPDATE`, `DELETE`) with pending transaction diff highlighting before `COMMIT`.
* **View Level (Automated Recalculation):** Database Views are treated as dynamic query objects. Any structural change (`ALTER TABLE`) or row data modification (`INSERT`/`UPDATE`/`DELETE`) committed to underlying base tables automatically triggers a view context refresh (`REFRESH MATERIALIZED VIEW` or grid invalidation/requery) to ensure views reflect updated data automatically in real time.

### 4.3 Safety & Guardrails
* **Read-Only Profile Guard:** Profile-level toggle that blocks all mutation operations (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, LDAP writes) at the desktop client level.

---

## 5. Privacy-First Data Lens & Command Execution Safety Flow

### 5.1 Architecture & Local Model Runtime
* **100% Offline Inference:** Zero metadata or data row content is transmitted to external cloud APIs.
* **Model Engine:** Data Lens supports local **GGUF files only**, using `llama.cpp` through `llama-cpp-python`. Model selection and loading require a `.gguf` extension and GGUF file header, plus the existing source approval and SHA-256 integrity checks. Ollama, remote inference services, and other model file formats are not supported.
* **Model source:** Follow the [README source guidance](./README.md#install-and-run) and independently verify an organization-approved source. Source approval is an operator attestation, not automatic verification of publisher or geographic origin.
* **Current model catalog (2026-11-02):** SmolLM2 1.7B Instruct Q4_K_M; Google Gemma 3 1B and 4B Instruct Q4_K_M; Meta Llama 3.2 1B and 3B Instruct Q4_K_M; Meta Llama 3.1 8B Instruct Q4_0; Phi-3 Mini 4K Instruct Q4; Microsoft Phi-4 14B Q4_K_S. See the [README model list](./README.md#choosing-a-model) for selection guidance and [source catalog](./src/datasifter/ai/model_catalog.py) for exact pinned artifacts. Other compatible GGUF files can be selected locally; inclusion is not real-model certification.
* **Context Scrubber Layer:** The current filter workflow uses structural LDAP schema metadata rather than directory entry values. RDBMS schema context is a Phase 2 design target.

### 5.2 Command Review, Validation & Confirmation Engine
Data Lens sends natural-language requests to the approved local GGUF model and requires one
strict JSON intent: `search`, `proposed_mutation`, or `clarify`. Pydantic validates the
intent shape; one bounded repair attempt handles malformed JSON or schema violations, after
which the failure is surfaced. Search intents pass through the typed directory resolver and
are reviewed with their filter, base DN, scope, requested attributes, and result limit when
the AI confirmation preference is enabled (the default). Mutation intents are resolved to a
typed dry-run preview and require explicit user confirmation before the request field applies
them. The model cannot apply changes itself. Password and credential attributes are excluded
from model-generated changes and search results.

The intent prompt uses schema metadata and request text, not directory entry values. Raw
requests, prompts, and model responses are not retained in the Data Lens log. The separate
Directory Workbench mutation controls keep their explicit confirmation flow. SQL assistance
is planned for product-roadmap Phase 2. Target session commands (connect or switch to a saved
profile, disconnect, list targets, show the current target) are matched locally without the
model. Local directory-information commands also show connection details, count visible people
under a user-selected starting location, and read numeric statistics from `cn=monitor` when
available. Counts use backend-specific person filters and completed paged searches; access
controls may hide entries. Monitoring can use current access, a same-endpoint saved profile, or
one-time credentials, without changing the active session. Other monitoring APIs are not
implemented. Help and related errors point to these capabilities and their limitations.
One target session is active at a time; switching disconnects and clears the previous
session first. Searches and mutations require an established session. A metadata-only local
audit file records Data Lens mutation outcomes without requests, DNs, attribute values, or
credentials. Preview checks are repeated before writing, but LDAP does not make review and
execution atomic.
The internal help catalog exposes intent examples, effective aliases, backend differences,
and safety constraints through an in-process typed API and the Help menu. Typed LDAP results,
schema and safety issues, connectivity failures, and local-model output errors are translated
into copyable diagnostics with next steps. No HTTP or MCP endpoint is started.
LDIF imports are user-selected file operations with their own full-file validation, preview,
explicit confirmation, read-only guard, and ordered application; LDAP cannot guarantee
atomicity across all records in a file or all sub-operations in a modify record.

```text
Natural Language Input ──► Local GGUF ──► Strict Intent JSON ──► Typed Resolver
                                                                  │
                    ┌─────────────────────────────────────────────┼────────────────┐
                    ▼                                             ▼                ▼
             Search + review                            Mutation dry-run       Clarification
                    │                                    (no write)                 │
                    ▼                                                            User input
               LDAP Search
```

1. **Review / Confirmation Display:**
   * Resolved LDAP search filters are presented for review when the AI confirmation preference is enabled (default); disabling that preference is the existing fast path.
   * LDIF change records are never generated by Data Lens. An imported file is validated as a whole and its ordered operations are previewed before the user confirms execution.
2. **Interactive Editing & Live Syntax Validation:**
   * Users can edit the generated LDAP filter; the filter validator checks syntax before enabling execution.
   * The LDIF validator checks file structure, values, distinguished names, and supported change-record forms before any import write is submitted. Server schema and access-control validation still occurs during execution.
3. **Optional Confirmation Mode:**
   * A global or per-session preference can bypass review for generated LDAP searches. LDIF imports always require explicit confirmation.

---

## 6. Phased Development Roadmap

### Phase 1: LDAP Core + Data Lens (Alpha Implementation)
* Complete PyQt6 desktop application shell (`DataSifter 2.0`).
* Keyring vault integration & local configuration store.
* `ldap3` async connection management and DIT tree virtualization.
* Advanced transport security (TLS, mTLS, custom CAs, persistent TOFU certificate validation).
* LDIF v1 validation, confirmed full change-record import, and selected-entry export with validation.
* Data Lens local GGUF inference through llama.cpp for structured search, proposed-mutation, and clarification intents, using the current catalog or an approved compatible local GGUF file.
* Reviewed typed searches and mutation dry-run previews; the model cannot apply changes.

### Phase 2: RDBMS Expansion
* Plug-in database drivers (`pyodbc`, `pymysql`, `oracledb`, `psycopg`).
* Database Explorer tree view and interactive `QTableView` data grid.
* SQL Console (`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `DDL`).
* Automated view recalculation/refresh triggers on base table modifications.
* Planned Data Lens expansion for Dialect SQL generation & cryptic database error diagnosis.

### Phase 3: Provisioning
* Field mapping interface for provisioning requests.
* Explicit create, update, and deactivate actions with dry-run previews and audit logging.
* Provisioning target integrations to be defined; no vendor-specific connectors are committed.
* Scope is provisioning only, not a general-purpose or bidirectional data synchronization engine.

---

## 7. Packaging & Distribution

* **Native Standalone Binary Target:** Package the application for Windows, macOS, and Linux. The current project has a Windows PyInstaller build workflow; a fresh build and clean-target validation are still required. Other platforms and database-driver packaging are future targets, not verified releases.