# Publication and SignPath readiness

Status: 2026-10-16. Sources and inventoried visual resources are published at https://github.com/zepidem/imperio-aion. There is no complete OSS release or active SignPath integration; visual-resource permissions remain pending.

## Repository publication

- [x] Separate the application/updater from the private BDO repository without copying its history.
- [x] Include GPL-3.0-only for own code, dependency notices and source provenance.
- [x] Exclude captures, private keys and existing binary distributions; restored catalogs retain unresolved rights notices.
- [x] Inventory the images included at the maintainer's request; their permission remains unresolved (see RESOURCE_RIGHTS.md).
- [x] Prepare a Windows GitHub Actions workflow for source audit, offline tests and native compilation.
- [x] Prepare contribution, security, privacy and code signing documents.
- [x] Confirm repository name/owner: the maintainer created the public repository `zepidem/imperio-aion`.
- [x] Publish the prepared Aion tree to the separate remote repository. The first full source tree matches the verified local tree exactly. BDO visibility has not been changed.
- [x] Complete the hosted verification workflow: [run 38966883240](https://github.com/zepidem/imperio-aion/actions/runs/38966883240) passed for commit `8a4c906bb760368b2b66627d1b236c9573bf6453`, including all 69 offline tests and native-helper compilation. The initial YAML quoting error was fixed before this run.
- [ ] Configure branch rules and review requirements. Require the `source-checks` job; prevent forced updates to release history where supported.
- [x] Enable MFA on the maintainer's GitHub account. The owner supplied the GitHub success screen on 2026-10-16 confirming authenticator-app 2FA is enabled.
- [ ] Enable MFA on SignPath when the account is configured; verify GitHub and SignPath MFA for any additional signing team members.

## Complete installable release

- [ ] Resolve each resource's rights. The owner reports sending an NCSOFT request; no reply or permission has been recorded. Permission for fan use may differ from permission for source/fork/installer redistribution and from SignPath eligibility.
- [ ] Confirm tables/catalog sources or provide original licensed replacements.
- [x] Prepare portable complete packaging, eleven locked wheels, runtime data, vendor source and binary hash inventories. Native/transitive license review remains open.
- [ ] Complete the Windows build in a clean environment; verify GUI, missing-resource behavior, update, installation and removal.
- [ ] Verify the privacy notice and controls for update traffic, diagnostics, capture and system changes against the actual installer.
- [ ] Reconcile application/updater version metadata and build trust. Calculate signed artifact hashes only after signing.
- [ ] Publish a release built from the public commit, with matching source, notices and clear download instructions.

## Application

- [x] Homepage/download page supplied by owner: https://imperiobdo.com/aion2.
- [x] Confirm application contact name and email with the maintainer (2026-10-16); recorded in the separate application draft.
- [x] Confirm repository URL: https://github.com/zepidem/imperio-aion.
- [ ] Confirm initial team roles.
- [x] Record the public community invite supplied by the maintainer: https://discord.gg/2cQSFwVst. Membership counts, independent usage and download statistics were not verified.
- [ ] Publish the privacy policy and link a section titled "Code signing policy" from the homepage and download/release pages.
- [ ] Confirm all statements in the English application against the final release, then submit.
- [ ] Receive SignPath acceptance and configure verifiable builds and manual signature approvals.

Do not claim all requirements are fulfilled while these items remain open. Do not use the private BDO repository as the source URL for the separated OSS application.

Official requirements: https://signpath.org/terms.html. Form: https://signpath.org/apply.html.
