# Contributing

This repository contains the Império Aion desktop application and updater sources. The BDO website and backend are maintained separately. Read [README.md](README.md) for the current source-preparation limitations before proposing a release.

Submit changes through a pull request with the problem, intended behavior and relevant verification. Contributions to the project's own code must be compatible with GPL-3.0-only. Keep third-party license notices and record the origin and license of every new dependency or asset. A copyright credit alone does not establish redistribution rights.

Use synthetic data in tests. Do not attach real game packet captures, character histories, access tokens, signing keys or local configuration. See [SECURITY.md](SECURITY.md) for private reports.

Run `python tools/audit_repository.py` and `python tools/run_tests.py` with the dependencies in `requirements.txt`. The tests isolate local state and block outgoing socket connections. They do not validate the GUI, driver, installer or a complete distribution. On Windows, `python tools/build_native.py` compiles but does not run the native helper; it requires an unused `build/native` directory.

The source map records the import baseline and the current prepared file hash. When intentionally changing a mapped source, preserve its original provenance, update `prepared_sha256`, and explain the change in the pull request. Hashes record provenance; they are not code review or a security boundary.

Changes from contributors without direct commit access require maintainer review. Changes affecting CI, update trust, installation, capture, privacy, assets or dependencies deserve particular attention. The initial review owner is [zepidem](https://github.com/zepidem); CODEOWNERS does not by itself enforce branch protection.
