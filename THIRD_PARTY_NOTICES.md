# Third-party licensing

The [MIT license](./LICENSE) applies to original DataSifter code. It does not
relicense third-party libraries, bundled native components, or model weights.

## Application dependencies

- **PyQt6:** the installed distribution declares GPL-3.0-only. Unless you have an
  applicable commercial PyQt license, distributing a combined application using
  this distribution requires complying with its GPL obligations, including
  applicable corresponding-source and license-notice requirements. The MIT
  license on DataSifter's original code does not remove those obligations.
- **Qt (PyQt6-Qt6):** the installed distribution declares LGPL v3. Review the
  licenses for the particular Qt modules and plugins included in a distribution
  and satisfy applicable notices, source, and relinking/replacement obligations.
- **ldap3:** the installed distribution declares LGPL v3; its obligations also
  remain applicable when distributing it.
- Other runtime, local-AI, packaging, and development dependencies retain their
  own licenses. PyInstaller's bootloader exception does not waive the licenses
  of libraries bundled into an executable.

See [BOM.md](./BOM.md) and [bom.json](./bom.json) for observed package versions,
license metadata, and components. These inventories are not a license audit;
missing metadata is not permission to redistribute. Check upstream license
texts and preserve required notices before publishing binaries.

## Models

Downloaded GGUF files and their upstream model weights are licensed separately
by their respective publishers and quantizers. Some models use custom terms
instead of an open-source software license. Review the model card and terms
linked from the model manager before using or redistributing a model.

No model weights or standalone application binaries are included in this source
repository. Review licensing for each target build and distribution separately.
