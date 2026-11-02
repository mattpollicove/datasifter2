# DataSifter bill of materials

Application version: **0.005**
Generated: 2026-11-02T13:15:10.250938+00:00

Generated from the project manifest, installed distribution metadata, source files,
and available packaged native binaries. Exact versions reflect this build environment.
Includes direct and transitive runtime, local-AI, build, test, and BOM-tool dependencies.
Platform-conditioned dependencies for other operating systems are not installed here
and are not claimed as resolved. This is not a license audit or vulnerability scan.

When generated outside a build, packaged-file hashes describe the last available build,
not proof that those binaries incorporate current source changes.

Regenerate with `python tools\generate_bom.py`; standalone builds regenerate automatically.
Dependency relationships and complete SHA-256 hashes are in [bom.json](./bom.json).

## Python libraries

| Library | Installed version | Dependency groups | License metadata |
|---|---|---|---|
| altgraph | 0.17.5 | packaging | MIT |
| arrow | 1.4.0 | bom | Not declared |
| attrs | 26.1.0 | bom | MIT |
| boolean.py | 5.0 | bom | BSD-2-Clause |
| cffi | 2.1.1 | runtime | MIT-0 |
| colorama | 0.4.6 | dev | Not declared |
| cryptography | 50.0.2 | runtime | Apache-2.0 OR BSD-3-Clause |
| cyclonedx-python-lib | 11.12.0 | bom | Apache-2.0 |
| defusedxml | 0.7.1 | bom | PSFL |
| diskcache | 5.6.3 | local-ai | Apache 2.0 |
| fqdn | 1.6.0 | bom | MPL-2.0 |
| idna | 3.20 | bom | BSD-3-Clause |
| iniconfig | 2.3.1 | dev | MIT |
| isoduration | 20.11.0 | bom | Not declared |
| jaraco.classes | 3.4.0 | runtime | Not declared |
| jaraco.context | 6.1.2 | runtime | MIT |
| jaraco.functools | 4.6.0 | runtime | MIT |
| Jinja2 | 3.1.6 | local-ai | Not declared |
| jsonpointer | 3.7 | bom | Modified BSD License |
| jsonschema | 4.26.0 | bom | MIT |
| jsonschema-specifications | 2025.9.1 | bom | MIT |
| keyring | 25.7.0 | runtime | MIT |
| lark | 1.3.1 | bom | MIT |
| ldap3 | 2.9.1 | runtime | LGPL v3 |
| license-expression | 30.4.4 | bom | Apache-2.0 |
| llama_cpp_python | 0.3.36 | local-ai | MIT |
| MarkupSafe | 3.0.4 | local-ai | BSD-3-Clause |
| more-itertools | 11.2.1 | runtime | MIT |
| numpy | 2.5.4 | local-ai | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| packageurl-python | 0.17.6 | bom | MIT |
| packaging | 26.3 | bom, build-system, dev, packaging | Apache-2.0 OR BSD-2-Clause |
| pefile | 2024.8.26 | packaging | MIT |
| pluggy | 1.7.0 | dev | MIT |
| py-serializable | 2.1.0 | bom | Apache-2.0 |
| pyasn1 | 0.6.5 | runtime | BSD-2-Clause |
| pycparser | 3.12 | runtime | BSD-3-Clause |
| Pygments | 2.21.0 | dev | BSD-2-Clause |
| pyinstaller | 6.22.3 | packaging | GPLv2-or-later with a special exception which allows to use PyInstaller to build and distribute non-free programs (including commercial ones) |
| pyinstaller-hooks-contrib | 2026.9 | packaging | Not declared |
| PyQt6 | 6.12.0 | runtime | GPL-3.0-only |
| PyQt6-Qt6 | 6.12.0 | runtime | LGPL v3 |
| PyQt6_sip | 13.13.0 | runtime | BSD-2-Clause |
| pytest | 9.2.0 | dev | MIT |
| pytest-qt | 4.5.0 | dev | MIT |
| python-dateutil | 4.0.0 | bom | BSD-3-Clause |
| pywin32-ctypes | 0.2.3 | packaging, runtime | BSD-3-Clause |
| referencing | 0.37.0 | bom | MIT |
| rfc3339-validator | 0.1.4 | bom | MIT license |
| rfc3986-validator | 0.1.1 | bom | MIT license |
| rfc3987-syntax | 1.1.0 | bom | MIT |
| rpds-py | 2026.9.1 | bom | MIT |
| ruff | 0.17.3 | dev | MIT |
| setuptools | 84.2.1 | build-system, packaging | MIT |
| six | 1.17.0 | bom | MIT |
| sortedcontainers | 2.4.0 | bom | Apache 2.0 |
| typing_extensions | 4.16.0 | dev, local-ai | PSF-2.0 |
| tzdata | 2026.6 | bom | Apache-2.0 |
| uri-template | 1.3.0 | bom | MIT License |
| webcolors | 25.10.0 | bom | BSD-3-Clause |
| wheel | 0.48.0 | bom, build-system | MIT |

## Internal components and build/test sources

These file components are maintained with the application version; hashes identify
their exact contents. See the specification and README for feature descriptions.

| Component | Category |
|---|---|
| [src/datasifter/__init__.py](./src/datasifter/__init__.py) | source |
| [src/datasifter/__main__.py](./src/datasifter/__main__.py) | source |
| [src/datasifter/ai/__init__.py](./src/datasifter/ai/__init__.py) | source |
| [src/datasifter/ai/local_model.py](./src/datasifter/ai/local_model.py) | source |
| [src/datasifter/ai/model_catalog.py](./src/datasifter/ai/model_catalog.py) | source |
| [src/datasifter/build_info.py](./src/datasifter/build_info.py) | source |
| [src/datasifter/build_version.py](./src/datasifter/build_version.py) | source |
| [src/datasifter/config.py](./src/datasifter/config.py) | source |
| [src/datasifter/directory/__init__.py](./src/datasifter/directory/__init__.py) | source |
| [src/datasifter/directory/filters.py](./src/datasifter/directory/filters.py) | source |
| [src/datasifter/directory/service.py](./src/datasifter/directory/service.py) | source |
| [src/datasifter/directory/worker.py](./src/datasifter/directory/worker.py) | source |
| [src/datasifter/security/__init__.py](./src/datasifter/security/__init__.py) | source |
| [src/datasifter/security/certificates.py](./src/datasifter/security/certificates.py) | source |
| [src/datasifter/ui/__init__.py](./src/datasifter/ui/__init__.py) | source |
| [src/datasifter/ui/main_window.py](./src/datasifter/ui/main_window.py) | source |
| [src/datasifter/ui/model_dialog.py](./src/datasifter/ui/model_dialog.py) | source |
| [src/datasifter/ui/settings_dialog.py](./src/datasifter/ui/settings_dialog.py) | source |
| [tools/generate_bom.py](./tools/generate_bom.py) | source |
| [tests/test_bom.py](./tests/test_bom.py) | test |
| [tests/test_build_info.py](./tests/test_build_info.py) | test |
| [tests/test_build_version.py](./tests/test_build_version.py) | test |
| [tests/test_certificates.py](./tests/test_certificates.py) | test |
| [tests/test_config.py](./tests/test_config.py) | test |
| [tests/test_directory_service.py](./tests/test_directory_service.py) | test |
| [tests/test_filters.py](./tests/test_filters.py) | test |
| [tests/test_local_ai.py](./tests/test_local_ai.py) | test |
| [tests/test_model_catalog.py](./tests/test_model_catalog.py) | test |
| [tests/test_ui.py](./tests/test_ui.py) | test |
| [tests/test_worker.py](./tests/test_worker.py) | test |
| [datasifter.spec](./datasifter.spec) | build |
| [pyproject.toml](./pyproject.toml) | build |

## Interpreter and packaged native components

Build interpreter: CPython **3.15.2**.
Native DLLs/extensions include embedded Qt, cryptography, and llama.cpp components
where present. Filenames and hashes are observed, not inferred versions or licenses.
The enclosing Python distribution is listed above where identifiable; embedded
third-party versions/licenses require vendor build information and are not guessed.

| Packaged file | SHA-256 |
|---|---|
| `dist/DataSifter 2.0/_internal/_asyncio.pyd` | `fb0768cc8b00cee43638bea2770cae08f2076298ec3db07634818448b6ad67c9` |
| `dist/DataSifter 2.0/_internal/_bz2.pyd` | `2ae45b2ff932dc8a864c862d8150eb3f5b55e60e3e74ec554c4f52c4f1ad81e0` |
| `dist/DataSifter 2.0/_internal/_cffi_backend.cp315-win_amd64.pyd` | `41f528cda715179bed45be6913f48aa07ae34c2c04f7ebbd74740632096d213d` |
| `dist/DataSifter 2.0/_internal/_ctypes.pyd` | `8bb22628ae859877b1cf55381c1a8cf54bb0aa70c56295053bc3303262e21c87` |
| `dist/DataSifter 2.0/_internal/_decimal.pyd` | `93360ba1650bf5f1a673aac22d444ad2d33a039550926f325ad19dafd950e04c` |
| `dist/DataSifter 2.0/_internal/_hashlib.pyd` | `3cfb95355576805d6362b878d2207e254125887fce12ff8199becab34beb0694` |
| `dist/DataSifter 2.0/_internal/_lzma.pyd` | `d9033e94387aa70777d23514263d342486899256ffc14478be6bb1e6a0b05683` |
| `dist/DataSifter 2.0/_internal/_multiprocessing.pyd` | `df1c1a1226a481db6536b6ed697150bc6a34052e4c2cc2ddde59ebda84a68fd6` |
| `dist/DataSifter 2.0/_internal/_overlapped.pyd` | `077d6a9ce8a3d379a06d78ac4be2efcf35006fff6cd5be4cd5f574d83e7a9c01` |
| `dist/DataSifter 2.0/_internal/_queue.pyd` | `00206e5ea1be8b648ca07d1e68ee244a8d0ee1f4126661187dbe3551c68f6e3b` |
| `dist/DataSifter 2.0/_internal/_socket.pyd` | `ba52c110c0cbc429add306acccf86dd2f77c777a9db68df7cddfc4489e91bce7` |
| `dist/DataSifter 2.0/_internal/_sqlite3.pyd` | `851e8bfa65328162ddad2dff9f7c213f8eceb2d90baf179aa472704ec3ca6295` |
| `dist/DataSifter 2.0/_internal/_ssl.pyd` | `a9c54ec4d115597880e0e44ff26b740328d0388a777dfb613d3aa2eb66b31e49` |
| `dist/DataSifter 2.0/_internal/_uuid.pyd` | `a3380826957922fae7350f5ea64fface688bf55c79e086823a4b1d50455a9bfa` |
| `dist/DataSifter 2.0/_internal/_wmi.pyd` | `0650bf237b3f556ba844ba372fef6de1b65d618a608014405360aef83f8a4e34` |
| `dist/DataSifter 2.0/_internal/_zstd.pyd` | `a977471edf7b6712a9e89ada98b79b1973c79740632f820b67365f242b5e05cc` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-console-l1-1-0.dll` | `a117b9488bd6111a7f77677830f3af5f6312a1f39d39927abe824375a4dd92fa` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-datetime-l1-1-0.dll` | `6313c373d4330e4acfa8e1e06f63bcd7ed34f94807ebeb856e6ca3b6f27d6f85` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-debug-l1-1-0.dll` | `c2fe516dd487b6030e60511f0d81f95183d32c5fc5fe98d8ea144c34781fc003` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-errorhandling-l1-1-0.dll` | `c99233e0c5a92f4bddf4fa8ffbdd9d75265b69c2096591b2016fa0ee4268c06a` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-fibers-l1-1-0.dll` | `36ffe216b07d3ad4d00a1e84f5fe640789a7e600b082c4f43e78be54c5f20505` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-file-l1-1-0.dll` | `5b7bc067babca352cdb283373770fe7da085226d6f024dee17664226b21f2962` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-file-l1-2-0.dll` | `f113a7b359889f87158e27a882930d73cc3a9df7f73216274e7fe64075ba4a14` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-file-l2-1-0.dll` | `7dba105af76154d437fd0c1f942fccc86e481e1d2e9a3f7ea767eb7b0122eafa` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-handle-l1-1-0.dll` | `53a4ddc354cc6b264a722833f402301009fd4a9091afe1babdd7144adc66f3e7` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-heap-l1-1-0.dll` | `c605856f77e404b15940cc17ef09eff274939db1bb97d4b22278b1cf75c41096` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-interlocked-l1-1-0.dll` | `f5e2ee1b6b2abd9695ccc72a493432bceef2806bfb6ac97e3eb333b99b7a2ffb` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-libraryloader-l1-1-0.dll` | `daf7d8310ed34a97ca36561833c4668e101200f95d42d2452b85527c5cea101b` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-localization-l1-2-0.dll` | `c4e59f279fa6a0782212a878ea3eaf071050b821c6bb8e196c20d06ba0b32309` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-memory-l1-1-0.dll` | `f6e56efddb200ab4d44318c3fd8801a3b82e37eb815f26df4b991ac85b9f7d31` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-namedpipe-l1-1-0.dll` | `9ae18f8d83027c6bc35da51d9278f862148fec5b003f49fc73c31b4420a016b2` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-processenvironment-l1-1-0.dll` | `84ddf1c1db481339987cf099078ec4494c3c377c0299a46ae87c0ac2bbab04bc` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-processthreads-l1-1-0.dll` | `c6110674f118b5eeeff5babb7d584b63f3d4c762cf7734969bc6aaaeccc0b408` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-processthreads-l1-1-1.dll` | `e220b4af54606ae96e808f562c43b1fe767496b05a3cb084a9315033378fee68` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-profile-l1-1-0.dll` | `d5b8c1b9251059fd8cdccd57b564aa406f216f297c01c6e3fbb1cf71b1e3f32f` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-rtlsupport-l1-1-0.dll` | `627cb3973cc66612a69f8a64e6caca8944b0a93e337487d2bd97eab50242d699` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-string-l1-1-0.dll` | `0f1b5ea355da93cbe7cb75380c9062f8937d94d5c486ef33dfdd6b4bdde04e6d` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-synch-l1-1-0.dll` | `611d898dcb1f710e2b1769db1729bbdd95602c9b8650f6bf2f572fcfebf96d19` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-synch-l1-2-0.dll` | `0c47108194cea7d549af81f226b6cc49d242daf2d9bdffb3faebdf9fe67bb148` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-sysinfo-l1-1-0.dll` | `8482e96387aa67faaf5008094293743a374212b8067561d5813976b00013ef72` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-timezone-l1-1-0.dll` | `97187cf4c59f08b0d5e731d08aba14b4e0e3056d804143919fad8b3549e979f0` |
| `dist/DataSifter 2.0/_internal/api-ms-win-core-util-l1-1-0.dll` | `db8ae9a98e8d414991a4831e2ea47e9530b854ea423806caa2ec9054e839da08` |
| `dist/DataSifter 2.0/_internal/api-ms-win-crt-conio-l1-1-0.dll` | `f34f0b3b865e1905e03df37b55537a04a3dd64d7efe8e02dacc5cdd31c7f559f` |
| `dist/DataSifter 2.0/_internal/api-ms-win-crt-convert-l1-1-0.dll` | `a23f79dea170f13580f1f6fa79e287289f61d9b767a06c2f759428150c1d0e2d` |
| `dist/DataSifter 2.0/_internal/api-ms-win-crt-environment-l1-1-0.dll` | `b73d0cb7f26c5975ff749f93b20d49032d8c31a6aba5093bad16519251b481bd` |
| `dist/DataSifter 2.0/_internal/api-ms-win-crt-filesystem-l1-1-0.dll` | `2ef6bf444bc61eeceb1207d1dad79be5e4eb28984a80c49bdbb2ad65c2cea875` |
| `dist/DataSifter 2.0/_internal/api-ms-win-crt-heap-l1-1-0.dll` | `1e913633caf31bc18faaea205569694b358c72c17dc93735c492ee40f7dc7e84` |
| `dist/DataSifter 2.0/_internal/api-ms-win-crt-locale-l1-1-0.dll` | `7b66fa037fd3f71371b3ad9967f2cb3bf112c8663b90c0e76ceec8bd6d594a15` |
| `dist/DataSifter 2.0/_internal/api-ms-win-crt-math-l1-1-0.dll` | `cb6629ffac613c87224ac7f0135f144e8daf9b03886019cf112c018de3c24be8` |
| `dist/DataSifter 2.0/_internal/api-ms-win-crt-private-l1-1-0.dll` | `ca27302931e60202caee6f54fb07d8796a748f58629d83f6272636a7e02da34b` |
| `dist/DataSifter 2.0/_internal/api-ms-win-crt-process-l1-1-0.dll` | `c570c8156d0dbb5a564c2e67e11466e5a2d154baaaf3e0128370cc45edc2cd43` |
| `dist/DataSifter 2.0/_internal/api-ms-win-crt-runtime-l1-1-0.dll` | `b0981bf692102f9e35fcb1b6c5794bb428da1bbf91d6b611514b0c6fb3d2d401` |
| `dist/DataSifter 2.0/_internal/api-ms-win-crt-stdio-l1-1-0.dll` | `3d3e2ac61d553a87e8756b595f0accbcce0d9cbbee988bf5bb054ddc7b0abb70` |
| `dist/DataSifter 2.0/_internal/api-ms-win-crt-string-l1-1-0.dll` | `53e0c388e407fde47b29189e2fefef7781c38d33748acff7892d078735c64a45` |
| `dist/DataSifter 2.0/_internal/api-ms-win-crt-time-l1-1-0.dll` | `d45cbd09183298eae8a83deda10ef3f33950c55f98d8c23f57080db63dbdf7dd` |
| `dist/DataSifter 2.0/_internal/api-ms-win-crt-utility-l1-1-0.dll` | `d33eff37f70c7a4b8a69b744087c5be130171eb886ebb31f6757dde1add0b58f` |
| `dist/DataSifter 2.0/_internal/cryptography/hazmat/bindings/_rust.pyd` | `4ee475cea79ddf074daf11ce0acc997101b3ebf49471d991c3695639994ff32a` |
| `dist/DataSifter 2.0/_internal/libcrypto-3.dll` | `75495fe0e079ab00d49be740d3c710a0b94e3f74f9648cb7cc9f768fc7f52319` |
| `dist/DataSifter 2.0/_internal/libffi-8.dll` | `eff52743773eb550fcc6ce3efc37c85724502233b6b002a35496d828bd7b280a` |
| `dist/DataSifter 2.0/_internal/libssl-3.dll` | `8957182a9c385bfd9f3f790921de2ddc614cf91f99edbed4e9e5446a46504e32` |
| `dist/DataSifter 2.0/_internal/markupsafe/_speedups.cp315-win_amd64.pyd` | `9fa1dbc3557429dbff7e50f0b162a5ba06904910088f1c2277a6965b9165cb86` |
| `dist/DataSifter 2.0/_internal/numpy/_core/_multiarray_tests.cp315-win_amd64.pyd` | `020f9b5498fc78ca96f9e1b42521b4d06ebbb3058c7f8f0938dacd68cd18148a` |
| `dist/DataSifter 2.0/_internal/numpy/_core/_multiarray_umath.cp315-win_amd64.pyd` | `b9476028d375e0c48ed6870284a61e87664c1181589ac5ff95dbc97469bb41c3` |
| `dist/DataSifter 2.0/_internal/numpy/fft/_pocketfft_umath.cp315-win_amd64.pyd` | `08ea588ac0b2b044a7767c7d95aa83627432c9b26ffe22ec363b7da8ecd920b3` |
| `dist/DataSifter 2.0/_internal/numpy/linalg/_umath_linalg.cp315-win_amd64.pyd` | `1589ca32f2a0592a154fb2fbedf071c69d359daa3619842104333bdf2d34bf1d` |
| `dist/DataSifter 2.0/_internal/numpy/random/_bounded_integers.cp315-win_amd64.pyd` | `316602d3b8283d7744c2f34eb6effd27565892f4544417dc66c26bd9d694d7a7` |
| `dist/DataSifter 2.0/_internal/numpy/random/_common.cp315-win_amd64.pyd` | `2d8fc3d8fbe4c010f7cecb10e1e872e245af5bc563a881cb30bd0b483ecded43` |
| `dist/DataSifter 2.0/_internal/numpy/random/_generator.cp315-win_amd64.pyd` | `008d36ae909c0ce7dad4accb0516f123931633e56e16e7f425248257dd6fce89` |
| `dist/DataSifter 2.0/_internal/numpy/random/_mt19937.cp315-win_amd64.pyd` | `2ff31fac465c0a78feae7b9b2dc612a22d3a4387c86181c72f13e923b6e46590` |
| `dist/DataSifter 2.0/_internal/numpy/random/_pcg64.cp315-win_amd64.pyd` | `a8a27a9e17b3e3420793f1845155c93fedded7f6569d2d2b5f42f0787843d4d7` |
| `dist/DataSifter 2.0/_internal/numpy/random/_philox.cp315-win_amd64.pyd` | `6bba31379c5f3a4f7d01012bbea9c08fdd5df0a290b6e79c4f18869faa4fb75f` |
| `dist/DataSifter 2.0/_internal/numpy/random/_sfc64.cp315-win_amd64.pyd` | `c069650f8a9bcbea5e5ab1b7622a38e1a50edeba021ccebf3dc09d337ba9e674` |
| `dist/DataSifter 2.0/_internal/numpy/random/bit_generator.cp315-win_amd64.pyd` | `72396774d9274b68a543472aa9e8d3ad451cbec08014cf5ee7365975da05cbbc` |
| `dist/DataSifter 2.0/_internal/numpy/random/mtrand.cp315-win_amd64.pyd` | `77b3b361232b5cb91d21e00c09976247b1725e7c566ab9dfe1a48043b9472f89` |
| `dist/DataSifter 2.0/_internal/numpy.libs/libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll` | `ed4f167a5330424524f45258e7ca2c8d7f03c0cba1f30ee4985553ac1f88ecf8` |
| `dist/DataSifter 2.0/_internal/numpy.libs/msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll` | `a4c2229bdc2a2a630acdc095b4d86008e5c3e3bc7773174354f3da4f5beb9cde` |
| `dist/DataSifter 2.0/_internal/pyexpat.pyd` | `2970cef7642e14721b4791bf40720d25f5c9fb8df4d78c725bd859391d6dec51` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/bin/MSVCP140.dll` | `0f885b509a685d2bbfa652fed26b5fb31d88fbdab0a978c641d1c7b8aa460aa9` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/bin/MSVCP140_1.dll` | `bfad5aef4c63a669e3c140655cdfdf395b6c979b400a447bd5dcb65ed8826c3d` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/bin/MSVCP140_2.dll` | `3ea06f0ee098b4823cb79599df3780e7f23cce52c19aac31d2a0d47efe33a5e9` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/bin/opengl32sw.dll` | `b04de4541863bc7d8879040a78889c4849c1b1da2784c4630f734c146c2998ce` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/bin/Qt6Core.dll` | `39175459c4837c8cc0f709a637fb6eadb25d696785109c8393559ec876f02703` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/bin/Qt6Gui.dll` | `37c79b48b9cc2d9a03d21410c26fea683d236f9280f5c9aaab85d980759f2835` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/bin/Qt6Network.dll` | `9825558f184c64f639c4b8663650cee0ae8ffe01c18d98ff76f006a02389965f` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/bin/Qt6Svg.dll` | `cce84059f1857ff1859db7c4fe10334e603d9ca81eb4d5f0a22ca0ac515b340c` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/bin/Qt6Widgets.dll` | `74138ca203c378a62367cc398e4dac07dec729b5a121486dbb26109854c17ae5` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/bin/VCRUNTIME140.dll` | `d5e4d9a3e835fa679450145d6a7d94e36573a509317111904d9b3712c30d9066` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/bin/VCRUNTIME140_1.dll` | `1f2d41c4aa5db0bc33ebf7b66d72943a817d7ce6cbe880502a9403823633093f` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/plugins/generic/qtuiotouchplugin.dll` | `5de903fa0ca5680243c01b8ed7ad0e6c27a6d00c7b29e0a51f581d32e0cdcde7` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/plugins/iconengines/qsvgicon.dll` | `09d02f929b537f9ba83834651b17c70fd89dcacbe22012b9fae3b529efa9c390` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/plugins/imageformats/qgif.dll` | `a1ea6be36c9aedbc54bf88bf391be85f1477d1d80f831f6eaf6a4d10fac8d027` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/plugins/imageformats/qicns.dll` | `1b05278d3e8997d1527a2e65f0806e33279088a92ff0c983164cce99dc78adc3` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/plugins/imageformats/qico.dll` | `964b39989a4a8dccd809c39b8e7d5813990aa31676d1d39b18aeea97336ddab7` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/plugins/imageformats/qjpeg.dll` | `9e67dc18c9f67141f5da20646b63a5ee4a57f8cfdad6b1b3c4c5f70e49ace065` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/plugins/imageformats/qsvg.dll` | `e8f2c80df916b363738d6f6dc112ac4555a42278347e78c0b609a55d1adf70cb` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/plugins/imageformats/qtga.dll` | `d103ddb4c0609f7d1bfe2c788745f1009cae2cd3c5827ef108fe4c8c535dfb3d` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/plugins/imageformats/qtiff.dll` | `f92d403b82755884bc0e20d924da8fd4328426c1c19d4f7b5677ca60b8585bf0` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/plugins/imageformats/qwbmp.dll` | `8225f681ae69327ee519c58b7a9b4d315316584fb9b75c554bd48943f0a3d2ae` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/plugins/imageformats/qwebp.dll` | `237afef6be8253fd57559516b2ba2dbeafdccb72b7416ad41f9fd15548f75172` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/plugins/platforms/qminimal.dll` | `cd0ad6b1c10f0870d3313055768f5bb3be1024ed23db554832fdf036331e98f6` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/plugins/platforms/qoffscreen.dll` | `54b0bc9a4aa9b1e1406e359569a8f2858ab368255f1e53c71d1fc405c2dbd32b` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/plugins/platforms/qwindows.dll` | `12eef54bcdb6711b2722e9cef983d91a54546565a96b684d7bd9e2a7370fde92` |
| `dist/DataSifter 2.0/_internal/PyQt6/Qt6/plugins/styles/qmodernwindowsstyle.dll` | `db5ef23d50d53fa66920baea1240a17dd47c6a6037dc72ee0288b07a3396b2c9` |
| `dist/DataSifter 2.0/_internal/PyQt6/QtCore.pyd` | `e1f485f6ed7357874309f468b34886ff55853f156712555e2cd5a9611cca17e7` |
| `dist/DataSifter 2.0/_internal/PyQt6/QtGui.pyd` | `e39e356a92c733d51f20f4dc4a394a8bd305700f2d48a4d3d5dcc1f60f3e779f` |
| `dist/DataSifter 2.0/_internal/PyQt6/QtWidgets.pyd` | `f76652920122f80be0ca3d1f8428e84be79bacb904b68edb237cfeecf3d2ea3a` |
| `dist/DataSifter 2.0/_internal/PyQt6/sip.cp315-win_amd64.pyd` | `c5dce44c32e7aa1046c23cb99aa16b9a6f880f02a8a6426d4c2f9cbdbb9bf9c6` |
| `dist/DataSifter 2.0/_internal/python3.dll` | `e394c32bb9e2f0d813f4ed5b858ba717e54e3e6547cd36a8d8aaee66b9ca347f` |
| `dist/DataSifter 2.0/_internal/python315.dll` | `9862572fd449f32a62c1eaec2c6750a9c9fa512f4c9c2ea0e66776cf9212b7c2` |
| `dist/DataSifter 2.0/_internal/python3t.dll` | `c4c1db006228553e4b728b7856b74a007384e608c7b07e6ae8e0d8251d077e5b` |
| `dist/DataSifter 2.0/_internal/select.pyd` | `d39e00967809d2d2cf8aea78f6fd39923cfb4ad426ccd19bbfe7163020dafffe` |
| `dist/DataSifter 2.0/_internal/sqlite3.dll` | `73530e8f4555605d716352ee98fe0ce205a09e5eec57e7e6360d4c915efcacd8` |
| `dist/DataSifter 2.0/_internal/ucrtbase.dll` | `45972b6f719d3bb20b667f184a3bb3cf5540e42c081e1392ac501fb4046e61a0` |
| `dist/DataSifter 2.0/_internal/unicodedata.pyd` | `ba69f643918cc520331c6a13d489d642131be23dd649d05e0cd1068fae92e76d` |
| `dist/DataSifter 2.0/_internal/VCRUNTIME140.dll` | `d1f4225df2cd877dbf130d5668a021dce3f94118455ff5ec952061c30afc9ce7` |
| `dist/DataSifter 2.0/_internal/VCRUNTIME140_1.dll` | `a7146c08f89fe5b04541ab507cdb59ff7b44534d4ba3c668a426c6450a03434e` |
| `dist/DataSifter 2.0/DataSifter 2.0.exe` | `a3cc29ada69ddee7fce6355963faed78c781ab92395df7b772f7463a48e0eccb` |

## External inputs and services

- LDAP v3 directory: user-provided endpoint; server product/version is not known.
- GGUF model: optional user-selected file; no model is shipped or inventoried.
- OS credential vault and certificate trust facilities: platform-provided services.
- Python standard library: part of the CPython runtime listed above.
