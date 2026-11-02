# Project DataSifter 2.0: Master Technical Specification

## 1. System Overview & Scope
* **Project Name:** **DataSifter 2.0**
* **Target Operating Systems:** Windows 10/11, macOS 12+, Linux (X11 & Wayland on GNOME/KDE).
* **Delivery Architecture:** **Native Thick Client Desktop Application** (compiled standalone binary via PyInstaller/Nuitka). Designed specifically for on-premises enterprise environments, direct local network/VPN socket connectivity, and zero intermediate cloud/server infrastructure.
* **Supported Data Sources:**
  * **Directory Services (LDAP v3):** Active Directory, OpenLDAP, PingDirectory, FreeIPA, 389 Directory Server.
  * **Relational Databases (RDBMS - Phase 2):** Microsoft SQL Server, MySQL, MariaDB, Oracle Database, PostgreSQL.
* **Technology Stack:**
  * **GUI Framework:** Python 3.10+, **PyQt6** (Native GUI Framework).
  * **Directory Engine:** pure-Python `ldap3` library.
  * **Database Engines & Drivers (Phase 2):** `pyodbc` (SQL Server), `pymysql` (MySQL), `oracledb` (Oracle thin driver), `psycopg` (PostgreSQL), `SQLAlchemy` (core query abstraction & schema reflection).
  * **Secrets Vault:** `keyring` (OS-native secret storage).
* **Protocol Support:** Fully compliant LDAP v3 (RFC 4511) and native RDBMS wire protocols.

---

## 2. System Architecture, Threading & Configuration

### 2.1 Multi-Engine Async Worker Model
* **Thread-Isolated Execution:** All network I/O, heavy SQL queries, directory searches, schema reflection, and local AI token streaming run off the main PyQt6 GUI thread using a `QThreadPool` / `QThread` worker pattern to prevent application freezes.
* **Connection Thread Safety:** 
  * **LDAP:** Isolated connection instances per worker task to guarantee socket thread safety.
  * **RDBMS:** Dedicated background session pools to prevent socket interleaving across concurrent UI operations.
* **Task Cancellation & Progress:** Real-time progress indicators, status updates, and cancellation tokens to safely abort long-running tasks.

### 2.2 Secure Credential Storage & Local Configuration
* **OS-Native Keyring Integration:** Store connection passwords securely via the `keyring` Python library (Windows Credential Manager, macOS Keychain, Freedesktop Secret Service API / KWallet). Zero plain-text password storage on disk.
* **Local Configuration Store:** Non-sensitive profile metadata (connection endpoints, ports, SSL/TLS preferences, database names, TOFU certificate fingerprints, AI settings, confirmation preferences, and UI states) are stored in structured JSON or lightweight SQLite files within platform-specific user directories:
  * **Windows:** `%APPDATA%\DataSifter\`
  * **macOS / Linux:** `~/.config/datasifter/`

### 2.3 Advanced Security & Certificate Infrastructure
* **LDAP Transport Security:** Supports `Plain` (Port 389), `StartTLS` (Port 389), and `Implicit LDAPS` (Port 636) with strict hostname/Subject Alternative Name (SAN) matching (`ssl.CERT_REQUIRED`).
* **RDBMS TLS/SSL Support:** Native SSL mode configuration per database engine (e.g., `sslmode=require` for Postgres, Encrypted connection strings for SQL Server, Oracle Wallet/TCPS).
* **Client Certificate Support (mTLS):** Optional configuration for local client certificates (`.crt`/`.pem`), private keys (`.key`), and passphrases for environments utilizing certificate-based binding or SASL `EXTERNAL`.
* **Self-Signed Certificates & TOFU (Trust-on-First-Use):**
  * Full compatibility with self-signed and internal CAs.
  * **Non-Production Warning:** The application interface explicitly displays a banner stating: **"Self-signed certificates are recommended for non-production environments only."**
  * **Interactive & Persistent Trust:** Unrecognized or self-signed certificates trigger an interactive modal displaying SHA-256 fingerprints, issuer details, subject, and expiration dates. Accepted fingerprints are stored locally within the profile configuration; subsequent connections validate against this stored fingerprint to instantly alert users of any certificate rotation or interception.

---

## 3. Data Source Workspaces & UI Layout

### 3.1 Dual-Mode Navigation Sidebar
The UI features a persistent primary dock allowing users to switch cleanly between data paradigms:
1. **Directory Information Tree (DIT) View (LDAP Mode - Phase 1):** Hierarchical container tree with virtualized node chunking (loading child nodes in paginated batches of 500) to safely display high-density containers.
2. **Database Explorer View (RDBMS Mode - Phase 2):** Hierarchical Object Tree displaying Databases $\rightarrow$ Schemas $\rightarrow$ Tables / Views $\rightarrow$ Columns, Indexes, and Constraints.

### 3.2 Workspace & Tabbed Interface
The central main window uses a multi-tab workspace:
* **Attribute & Grid Editor:** Render directory entry attributes or table data rows with inline editing capabilities.
* **SQL Console / Filter Bar:** Dialect-aware SQL execution editor and LDAP raw filter engine with syntax highlighting.
* **Schema Inspector:** Side-by-side view of directory object classes/syntaxes vs. RDBMS DDL schemas, primary/foreign keys, and data types.
* **Protocol & SQL Log Console:** Live stream of outbound LDAP packets (`ldap3` dictionaries) and executed raw SQL statements with execution timing metrics.
* **Directory Lens:** Interactive local assistant sidebar.

---

## 4. Data Object Scope & Operations (CRUD per Source)

### 4.1 LDAP Data Operations (Phase 1 Core)
* **Read / View:** Display attributes and values with a toggle for **Operational Attributes** (`createTimestamp`, `creatorsName`, `modifiersName`, `modifyTimestamp`, `entryUUID`).
* **Create & Update:** Add entries guided by structural/auxiliary schema object classes; modify attributes via Add, Replace, or Delete operations.
* **Delete:** Remove entries with confirmation dialogs and optional recursive subtree deletion protection.
* **ModRDN / Move:** Rename entry RDN or move across containers with `deleteOldRDN` support.
* **Password Management (RFC 3062):** Dedicated utility utilizing LDAP Password Modify Extended Operations for secure user password resets/changes.

### 4.2 Database Objects & Update Rules (Phase 2 Core)
* **Database Level:** Create, drop, inspect, and alter database properties/collations.
* **Table Level:** Create, alter (add/modify/drop columns), truncate, and drop tables.
* **Row Level:** Direct DML operations (`INSERT`, `UPDATE`, `DELETE`) with pending transaction diff highlighting before `COMMIT`.
* **View Level (Automated Recalculation):** Database Views are treated as dynamic query objects. Any structural change (`ALTER TABLE`) or row data modification (`INSERT`/`UPDATE`/`DELETE`) committed to underlying base tables automatically triggers a view context refresh (`REFRESH MATERIALIZED VIEW` or grid invalidation/requery) to ensure views reflect updated data automatically in real time.

### 4.3 Safety & Guardrails
* **Read-Only Profile Guard:** Profile-level toggle that blocks all mutation operations (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, LDAP writes) at the desktop client level.

---

## 5. Privacy-First Directory Lens & Command Execution Safety Flow

### 5.1 Architecture & Non-Chinese Model Runtime
* **100% Offline Inference:** Zero metadata or data row content is transmitted to external cloud APIs.
* **Model Engine:** Directory Lens supports local **GGUF files only**, using `llama.cpp` through `llama-cpp-python`. Model selection and loading require a `.gguf` extension and GGUF file header, plus the existing source approval and SHA-256 integrity checks. Ollama, remote inference services, and other model file formats are not supported.
* **Current model catalog (2026-11-02):** SmolLM2 1.7B Instruct Q4_K_M; Google Gemma 3 1B and 4B Instruct Q4_K_M; Meta Llama 3.2 1B and 3B Instruct Q4_K_M; Meta Llama 3.1 8B Instruct Q4_0; Phi-3 Mini 4K Instruct Q4; Microsoft Phi-4 14B Q4_K_S. See the [README model list](./README.md#choosing-a-model) for selection guidance and [source catalog](./src/datasifter/ai/model_catalog.py) for exact pinned artifacts. Other compatible GGUF files can be selected locally; inclusion is not real-model certification.
* **Context Scrubber Layer:** Middleware that strips sensitive data row values before prompting, providing the AI strictly with structural schema metadata (table DDL, column types, LDAP object classes).

### 5.2 Command Review, Validation & Confirmation Engine
To prevent unintended data loss or syntax errors caused by natural language processing, all AI-generated operations strictly execute through a multi-stage validation pipeline:

```text
Natural Language Input ──► AI Translation ──► Review & Confirmation Modal
                                                      │
                                           ┌──────────┴──────────┐
                                           ▼                     ▼
                                    Manual Edit &           Bypass Toggle
                                   Pre-flight Check         (Optional Direct Run)
                                           │                     │
                                           └──────────┬──────────┘
                                                      ▼
                                           Execution on Database/LDAP
```

1. **Mandatory Staging / Confirmation Display:**
   * After natural language processing, the generated raw SQL statement, LDAP search filter, or LDIF change record is presented in an explicit **Command Review Modal / Inspector Pane** prior to execution.
2. **Interactive Editing & Live Syntax Validation:**
   * Users have full ability to manually edit and fine-tune generated command strings.
   * **Pre-flight Validator:** Integrated AST/regex parser checks edited queries in real time (verifying balanced parentheses, matching quotes, valid operators, and syntax compliance) before enabling the "Execute" button.
3. **Optional Confirmation Mode:**
   * A user setting (**"Require confirmation before executing AI commands"**) allows experienced users to bypass the review modal for fast-path execution. This preference can be toggled globally or per-session.

---

## 6. Phased Development Roadmap

### Phase 1: LDAP Core + Directory Lens (Alpha Implementation)
* Complete PyQt6 desktop application shell (`DataSifter 2.0`).
* Keyring vault integration & local configuration store.
* `ldap3` async connection management and DIT tree virtualization.
* Advanced transport security (TLS, mTLS, custom CAs, persistent TOFU certificate validation).
* Directory Lens local GGUF inference through llama.cpp for Natural Language $\rightarrow$ RFC 4515 LDAP Filters, using the current catalog or an approved compatible local GGUF file.
* Command Review Modal with pre-flight syntax validation and optional confirmation toggle.

### Phase 2: RDBMS Expansion
* Plug-in database drivers (`pyodbc`, `pymysql`, `oracledb`, `psycopg`).
* Database Explorer tree view and interactive `QTableView` data grid.
* SQL Console (`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `DDL`).
* Automated view recalculation/refresh triggers on base table modifications.
* Planned Directory Lens expansion for Dialect SQL generation & cryptic database error diagnosis.

### Phase 3: Data Synchronization Engine & SaaS Provisioning
* Drag-and-drop LDAP $\leftrightarrow$ RDBMS attribute/column visual mapping interface.
* Delta calculation engine & dry-run audit logger.
* SaaS IDP target connectors (Okta, Microsoft Entra ID via Graph API) for outbound synchronization pipelines.

---

## 7. Packaging & Distribution

* **Native Standalone Binary Compiling:** Packaged for deployment using **PyInstaller** or **Nuitka**, bundling the Python interpreter, PyQt6 runtime, database drivers, and dynamic OpenSSL system libraries (`libssl`/`libcrypto`) into clean, single-directory or single-file desktop executables across Windows, macOS, and Linux.