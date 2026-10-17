# Native components — Windows candidate, 16 October 2026

This review uses the actual locked CPython 3.14.7 x64 environment and wheel notices, rather than license labels alone. `binary-inventory.json` in each build lists outer files and binaries embedded inside the standalone installer, with hashes. Static dependencies are attributed through the upstream notices and SBOMs; they are not necessarily separate DLLs.

| Binary family | Components and selected terms | Notices shipped |
| --- | --- | --- |
| Python executable, standard extensions, Tcl/Tk | CPython 3.14.7, Tcl 9.0.4/Tk 9.0, libffi, bzip2, zlib, liblzma, zstd and Expat; PSF and respective permissive terms | Python-LICENSE.txt, including bundled-component notices |
| Python `_ssl`, `_hashlib`, libcrypto/libssl | OpenSSL 3.5.7, Apache-2.0 | native-review/openssl-3.5.7 and Python notice |
| Pillow extensions | Pillow 12.3.0 MIT-CMU; FreeType 2.14.3 (FTL option), HarfBuzz 14.2.1, Brotli 1.2.0, LittleCMS 2.19.1, libavif 1.4.2 and dav1d, libjpeg-turbo 3.1.4.1, libpng 1.6.58, libwebp 1.6.0, OpenJPEG 2.5.4, libtiff 4.7.1, xz 5.8.3 and zlib-ng 2.3.3 | Complete Pillow wheel LICENSE, including the nested component notices; FreeType credit in CREDITS.txt |
| cryptography `_rust.pyd` inside updater | cryptography 50.0.1, Apache-2.0/BSD; statically linked OpenSSL 4.0.2, Apache-2.0; Rust dependencies | cryptography wheel notices and upstream SBOMs, native-review component notices |
| CFFI backend, if collected | cffi 2.1.1 MIT-0, libffi MIT | Wheel notice and Python/libffi notice |
| PyInstaller bootloaders and runtime hooks | PyInstaller GPL with bootloader exception; runtime hooks Apache-2.0 where identified by upstream | Complete PyInstaller and hooks notices |
| VCRUNTIME140, VCRUNTIME140_1, UCRT and API-MS Windows runtime files | Microsoft Distributable Code conditions, as distributed with CPython; Windows system runtime components, not project GPL code | Microsoft conditions in Python-LICENSE.txt; redistribute unmodified for Windows |
| WinDivert.dll and WinDivert64.sys | WinDivert 2.2.2, LGPL-3.0 option; dynamically loaded | Full source archive, license and NOTICE beside the vendor DLL; original driver signature preserved |
| Monitor, updater and native installer helper | Project code GPL-3.0-only | PROJECT-GPL-3.0.txt and matching public source, build scripts and installation documentation |

The Rust inventory comes from cryptography's upstream CycloneDX build SBOM. Its 39 components include seven internal cryptography crates and 32 external crates, including build-only/other-target dependencies. We do not claim that every crate is linked into this Windows executable. External crate downloads were checked against cryptography's Cargo.lock checksums, and their license/notice files are preserved. Provenance is in `metadata/native-license-review.json`; unchanged upstream SBOMs are in `metadata/upstream-sboms/` and are also collected from the installed wheel into the build's licenses folder.

For alternatives, select the permissive option: Apache-2.0 for self_cell (rather than GPL-2.0-only), FTL for FreeType, and MIT or Apache-2.0 for crates offering those alternatives. Retain Unicode-3.0 notices for unicode-ident and the LLVM exception text for target-lexicon. These choices avoid treating a dual-license dependency as obligatorily GPL-2.0-only.

The installer includes these notices before installation; its **Licenças** link opens the bundled folder without a network request. Application packages also carry an external licenses folder. WinDivert source is additionally included beside the distributed library. Modified builds can be produced from the published sources with the builder and trust configuration; the project's signed update channel accepts only authorized releases. Do not strip LGPL rights to modify, replace or debug the library. Production installation and replacement behavior still require a real Windows acceptance test.

This is a documented component review, not a legal clearance of all bundled materials. The Windows runtime retains its vendor conditions. Game images/catalogs remain separately documented in RESOURCE_RIGHTS.md and are not relicensed by the project GPL. NCSOFT authorization is still pending. Website hosting/log retention and actual production deployment are outside this binary inventory.

Primary references: [CPython licenses](https://docs.python.org/3/license.html), [cryptography Windows wheels](https://cryptography.io/en/latest/installation/), [Pillow licensing](https://pillow.readthedocs.io/en/stable/about.html), [OpenSSL licensing](https://openssl-library.org/source/license/index.html). Version-specific evidence is the preserved build input and wheel notice, which may differ from these current documentation pages.
