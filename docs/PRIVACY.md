# Privacy policy — source preparation

Updated 2026-10-16 for the Windows candidate. This policy describes the reviewed application and installer paths. It is not an audit of the production website or hosting configuration. The hosting review remains open.

Project contact: **support@imperiobdo.com**. Maintainer: **Jose Mardegam (Zepidem)**. The Portuguese in-app notices are in `src/imperio_aion/dados/legal-pt.json` and `src/updater/legal.json`.

## Local game data

When it detects the game, the monitor can automatically prepare/start capture, requesting Windows authorization when needed. Closing the HUD ends capture. Enabling launch at Windows sign-in can therefore lead to capture preparation when the game is found. With capture active, the monitor reads eligible AION 2 TCP connections to display combat statistics, character and party information, activities, balances and pet progress. It also inspects process and connection metadata to associate the traffic with the game and window information to position its interface. The WinDivert adapter uses passive sniff/receive-only flags with exact endpoint filters. Capture requires Windows administrator permission.

Preferences, caches, combat sessions, exports and diagnostics are stored locally, normally under `%LOCALAPPDATA%\ImperioAION`. Diagnostics may include player identifiers, addresses, ports, local paths and uninterpreted game messages. Local files are not given additional application-level encryption. There is no general automatic expiry for all local history and exports. Close the application before deleting local data; deleting preferences resets configuration as well.

No automatic upload of combat sessions, diagnostics or player profiles was found in the reviewed monitor and updater paths. This is a source-review finding, not a claim about all third-party services or binaries. Saving a diagnostic creates a local file. If the user sends it to support, the recipient receives the content the user chooses to send. Review and minimize that material first.

## Downloads and update checks

The updater uses `https://imperiobdo.com/aion2/releases/v2/` over HTTPS. Automatic checks are **off by default**. The initial launcher validates the installed files locally and can open an authenticated, supported installation offline. The user can explicitly check manually or opt into automatic checks with the visible checkbox. The preference is saved locally and can be disabled without installing anything; a missing or malformed preference does not enable network access. Download and installation require their own user action.

Delivery servers receive IP address, request time, URL and User-Agent (`ImperioAion-Updater/0.4.2`). Reviewed transport requests do not attach combat sessions, local profiles or diagnostics. The UI discloses requests before the manual check, and the privacy notice is available before any automatic check is enabled. Opening external links deliberately makes requests to those services.

Live delivery headers confirm Cloudflare in front of Render. The maintainer confirmed Render Hobby: service logs are available in its explorer for 7 days, per https://render.com/docs/logging. This is not a promise that all infrastructure data or exported copies are deleted after 7 days. The reviewed Aion download routes do not write an application access log. Shared site middleware can receive CSP violation reports; it logs the directive and blocked hostname, omitting document URL, query and identity.

Cloudflare documents 31 days for Security Events across plans, and 31 days for Security Analytics on Free/Pro/Business (90 on Enterprise): https://developers.cloudflare.com/waf/analytics/security-events/. These are product-specific limits, not universal visitor-data retention. The account plan, other enabled logging products and external log exports were not available through the connected tools and remain unconfirmed. Provider policies: https://render.com/privacy and https://www.cloudflare.com/privacypolicy/.

## Optional streaming overlay

The overlay serves selected views on `127.0.0.1` for local OBS/Streamlabs browser sources. HUD and panel sources have independent access URLs and start disabled. Anyone with sufficient access to the machine or its saved URL may access the enabled view. When the user broadcasts it, the broadcasting software and selected platform process the visible content under their own policies. The monitor does not itself publish the broadcast.

## Support and user control

Do not send game passwords, authentication codes or payment details. Data voluntarily sent to support should be used to answer the request and retained only as necessary for that purpose or applicable obligations. Contact support for questions or requests concerning data handled by the project. Deleting local files does not delete copies previously shared with another party.

## Evidence and remaining review

Reviewed paths include `network_preferences.py`, `startup_flow.py`, `updater_app.py`, `update_service.py`, `update_core.py`, `app.py`, `native_windivert.py` and `streaming_server.py`, plus both shipped legal notices. Offline regression tests cover opt-in/out, malformed settings, local authentication and blocked corrupt installations. The frozen installer UI check verifies the initial opt-out and embedded notices. Native dependency notices are described in NATIVE_LICENSES.md. Hosting logs and production Windows installation remain acceptance checks; these source and isolated-build checks do not establish full SignPath eligibility.
