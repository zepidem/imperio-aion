# Privacy policy — source preparation

Prepared 2026-10-16 from the existing application notice and a targeted review of network entry points. This document describes the imported sources; it is not a verified description of a future complete installer or an audit of the production website. Before release, reconcile it with the final build and the hosting configuration.

Project contact: **support@imperiobdo.com**. Maintainer: **Zepidem**. The Portuguese in-app notices are in `src/imperio_aion/dados/legal-pt.json` and `src/updater/legal.json`.

## Local game data

When the user starts capture, the monitor reads eligible AION 2 TCP connections to display combat statistics, character and party information, activities, balances and pet progress. It also inspects process and connection metadata to associate the traffic with the game and window information to position its interface. The WinDivert adapter uses passive sniff/receive-only flags with exact endpoint filters. Capture requires Windows administrator permission.

Preferences, caches, combat sessions, exports and diagnostics are stored locally, normally under `%LOCALAPPDATA%\ImperioAION`. Diagnostics may include player identifiers, addresses, ports, local paths and uninterpreted game messages. Local files are not given additional application-level encryption. There is no general automatic expiry for all local history and exports. Close the application before deleting local data; deleting preferences resets configuration as well.

No automatic upload of combat sessions, diagnostics or player profiles was found in the reviewed monitor and updater paths. This is a source-review finding, not a claim about all third-party services or binaries. Saving a diagnostic creates a local file. If the user sends it to support, the recipient receives the content the user chooses to send. Review and minimize that material first.

## Downloads and update checks

The updater contacts `https://imperiobdo.com/aion2/releases/v2/` over HTTPS to fetch release metadata and installation packages. The normal launcher checks for updates before opening the application. Servers and delivery providers consequently receive connection and request information such as IP address, request time, URL and User-Agent. The imported code still identifies that User-Agent as `ImperioBDO-Updater/0.1`; it is a legacy identifier, not evidence of BDO code publication. These requests do not include combat sessions or diagnostic attachments in the reviewed transport.

The existing notice identifies Render and Cloudflare as delivery providers. Their configuration, log retention and current website behavior require confirmation before publication of the final policy. Provider information: https://render.com/privacy and https://www.cloudflare.com/privacypolicy/.

The present source should not be represented as having an installer option that disables every outbound update check: that control has not been verified. Assess and implement the required control before claiming compliance with SignPath's privacy conditions. Update installation itself is offered to the user.

## Optional streaming overlay

The overlay serves selected views on `127.0.0.1` for local OBS/Streamlabs browser sources. HUD and panel sources have independent access URLs and start disabled. Anyone with sufficient access to the machine or its saved URL may access the enabled view. When the user broadcasts it, the broadcasting software and selected platform process the visible content under their own policies. The monitor does not itself publish the broadcast.

## Support and user control

Do not send game passwords, authentication codes or payment details. Data voluntarily sent to support should be used to answer the request and retained only as necessary for that purpose or applicable obligations. Contact support for questions or requests concerning data handled by the project. Deleting local files does not delete copies previously shared with another party.

## Evidence and remaining review

Network and capture paths examined: `update_service.py`, `update_core.py`, `native_windivert.py`, `streaming_server.py`, plus the two existing legal notices. The final privacy review must cover installer controls, third-party components, hosting and the final binaries. This preparation does not assert that those checks have already passed.
