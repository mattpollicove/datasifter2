# Privacy and data handling

DataSifter is a local-first LDAP desktop client. It does not provide hosted user
accounts or remote inference. Normal use may send network requests to the LDAP
server you configure and, when you choose to download a catalog model, to its
model host.

## Directory connections

LDAP searches, updates, authentication, and connection checks go to the server
and endpoint you configure. Plain LDAP does not encrypt traffic; use a trusted
network or configure TLS. The directory server receives the requests and
credentials required for the selected operation, and its own privacy and
retention practices are outside DataSifter's control.

## Local profiles and credentials

Non-sensitive settings and profiles are stored locally. Passwords and optional
private-key passphrases are stored in the operating-system keyring only when
you choose to save them; otherwise, entered bind passwords are held in memory
for the session. Passwords are not written to profile JSON.

## Data Lens and models

Data Lens uses local GGUF inference to produce validated search, proposed-mutation,
or clarification intents. Prompts contain the request text and relevant schema names,
not directory entry values. Search intents follow the AI confirmation preference
(enabled by default); mutation intents require a dry-run preview and explicit user
confirmation before application. The model cannot apply changes itself.
Avoid including passwords or credentials in requests. The in-memory Data Lens log
records the model and intent category, not the request, prompt, or raw model response.
The optional, copyable AI explanation describes the validated interpretation and resolution.
It may display request values and directory details in the window but is not written to the log.
Target session commands such as `connect to <profile>` are matched locally and
are not sent to the model; the same in-memory log records them and the saved
profile names and hosts they report.
Choosing to download a catalog model sends a download request to its listed
model host; the model host's policies apply to that network request.

## Diagnostics and support

Diagnostic logs are stored locally, rotate, and contain sanitized LDAP protocol
events. Distinguished names and directory structure may remain visible, so
treat logs as sensitive and review them before sharing. Raw NLP requests,
prompts, and model responses are not persisted in these diagnostic files.
Data Lens mutation events are stored separately in a local JSON-lines audit file. They contain
timestamps, correlation IDs, operation types, backends, and outcomes, but no DNs, attribute
values, requests, credentials, or error messages. This file is not automatically rotated and
does not cover operations outside Data Lens.

For support, open a [GitHub Issue](https://github.com/mattpollicove/datasifter2/issues).
Redact credentials, directory entry data, and sensitive log details before
posting. The operating system, LDAP server, model host, and third-party
dependencies may have separate privacy practices.
