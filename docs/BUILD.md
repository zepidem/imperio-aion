# Windows build

Use Windows x64, CPython **3.14.7**, and a non-administrator account for QA.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --only-binary=:all: --require-hashes -r requirements-windows.lock
.venv\Scripts\python tools/audit_repository.py
.venv\Scripts\python tools/run_tests.py
.venv\Scripts\python tools/build_windows.py --output dist/windows-candidate
.venv\Scripts\python tools/verify_candidate.py dist/windows-candidate
```

Choose a new output directory for every build. Its matching build/<output-name> staging directory must also be new. Previous builds are never overwritten. GitHub Actions builds and verifies the full archive inventory (--inventory-only), storing successful candidates for 14 days. Hidden GUI and isolated install/remove QA must run separately as a non-administrator because Windows hosted runners may be elevated. CI does not publish a Release or modify the website.

## Build inputs

The monitor source reference is 0.8.71; the updater source reference is 0.4.2. Newly built project PE files share ProductName **Império Aion** and ProductVersion **0.8.71**. This is a candidate, not a replacement for an existing release.

Runtime data includes inventoried images, pet/NPC/skill catalogs, protocol names and the local OBS overlay. See RESOURCE_RIGHTS.md: restoring these files does not clear their rights. WinDivert 2.2.2 comes from upstream, with fixed archive and extracted-binary hashes. Only its x64 DLL/driver, complete source ZIP and notices are bundled. Pass --vendor-cache for an existing hash-verified cache.

requirements-windows.lock pins eleven runtime/build wheels by SHA-256 for this interpreter/platform. metadata/windows-wheels.json records filenames. Always install with hash checking; the builder's version check is an additional safeguard.

The monitor uses PyInstaller one-directory mode and the setup uses one-file mode. The C# helper is compiled from source. Its new hash is pinned only in staging; original updater trust files are not silently overwritten. Package notices include installed dependency licenses, Python, Tcl/Tk and WinDivert. The binary inventory is an exact hash inventory, not a complete legal clearance or bit-for-bit reproducibility claim.

## Outputs

- imperioaion-setup.exe: installer/updater using the existing configured HTTPS feed. It does **not** automatically install the candidate ZIP beside it.
- application/ and imperio-aion-0.8.71-candidate.zip: complete runtime, updater, driver, notices and catalogs.
- package-inventory.json, binary-inventory.json, build-environment.json and build-proof.json: hashes, environment and source provenance. A release build must have git_dirty=false.
- After QA: app-check.json, installer-ui-check.json and validation.json.

## Verification scope

The real frozen app renders its panel/HUD in hidden windows with fresh and saved synthetic state. It checks required images, 207 pets, 284 skills, NPCs, legal pages and DLL ABI/filter compilation. Capture and game probes are disabled. The setup renders without downloading.

A separate verifier is compiled with a fresh in-memory QA key. It validates the exact candidate, rejects a wrong signature, installs into a marked temporary directory, detects a modified installed executable and removes that isolated installation while preserving synthetic user data. QA private keys are never exported and this verifier is not delivered. No driver is started, registry entry created or Program Files installation performed. Elevated installation/ACLs and actual game capture still require separate validation.

## Signing and publication

The imported updater_trust.py identifies its anchors as a test/pilot channel. New binaries need matching signed metadata; existing channel metadata cannot authorize changed hashes. Production release authorization requires the maintainer's controlled keys. Do not bypass validation or distribute QA keys.

Authenticode changes file bytes. The future pipeline must sign the helper first, pin its final hash in the staged updater, build/sign the updater and monitor, then construct and authenticate the final ZIP. Only project-owned executables may be submitted to SignPath; preserve upstream signatures. Do not embed a signed helper with a pre-signing hash.

tools/check_release.py intentionally reports pending until resource permissions, privacy controls, release authorization, public release evidence and SignPath setup are resolved. Compilation does not grant signing approval.
