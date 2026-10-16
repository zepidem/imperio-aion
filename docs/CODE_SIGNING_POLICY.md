# Code signing policy

## Current status

Império Aion is preparing an application to SignPath Foundation. It has not been accepted and no binary from this repository is represented as signed by SignPath. The current workflow verifies sources, runs offline tests and compiles the native helper only. It does not build a complete distribution, request a signature or publish a release.

The following is the proposed process to implement before the first signed release, not a statement that the remote controls are already configured.

## Responsibilities

| Role | Initial proposed member | Responsibility |
| --- | --- | --- |
| Author / committer | [zepidem](https://github.com/zepidem) | Maintain application and build sources |
| Reviewer | [zepidem](https://github.com/zepidem) | Review external contributions before merge |
| Signing approver | [zepidem](https://github.com/zepidem) | Manually approve each signing request after reviewing its source and build evidence |

Confirm these role assignments when configuring the SignPath account. All members must enable MFA for repository and SignPath access before signing is enabled. CODEOWNERS is review routing, not evidence that repository rules are enforced.

## Proposed release process

1. Review the exact source commit, dependency licenses, resource permissions and completed release checklist.
2. Build on a clean Windows CI runner from that commit, using pinned build inputs. Record dependency hashes, source revision, build logs and artifact hashes. The present dependency inventory is not yet a complete reproducible build lock.
3. Submit only this project's eligible executable artifacts. Do not apply the project's signature to WinDivert, Python or other upstream binaries. Preserve upstream signatures and notices when packaging them.
4. Restrict product name and product version metadata in the signing configuration. Resolve the existing application/updater version scheme before configuring these restrictions.
5. Require a manual signing approval. Keep signing credentials unavailable to pull-request workflows. Do not upload private update-signing keys to the repository.
6. Generate the final package hashes and update metadata after Authenticode signing; a signature changes the executable bytes. Verify the package and publish matching source and release notes.
7. Publish the active policy on the homepage and download/release pages once the service is approved and configured.

The acknowledgment required after approval and activation is:

> Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by [SignPath Foundation](https://signpath.org).

Do not present that acknowledgment as an active service before approval.

## Privacy

See [Privacy policy](PRIVACY.md). Combat data is processed locally in the reviewed source. The updater makes HTTPS requests to the release service; a blanket statement that the software never contacts other systems would be inaccurate.

Program requirements: https://signpath.org/terms.html (consulted 2026-10-16). Acceptance remains a decision of SignPath Foundation.
