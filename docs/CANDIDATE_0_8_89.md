# Local corrective candidate 0.8.89

This candidate is unpublished. It does not claim that all character names
can be identified immediately when capture starts inside a dungeon.

## Changes

- Always label the proven local player as `Você` in the HUD, panel, history,
  skill heading and charts, before and after its real nickname arrives. Peer
  names remain unchanged. Preserve original names, IDs, actor scope, class,
  damage and selection keys; the label never supplies identity evidence.
- Keep the local marker of a named roster member tied to the original
  connection/roster epoch so historical fights retain `Você` after relog.
- Show the local character field as read-only `Você`; the actual automatically
  identified name remains in internal state, preferences and diagnostic exports.
- Remove the unproven attribution of 058a member-list names to the local actor.
  Member count, guild metadata, timing and saved names do not prove identity.
- Keep an explicit name already validated against independent party evidence
  in its current connection and roster epoch. If self stats later identify
  that same world actor as local, promote the existing name without waiting
  for another name packet. This works beyond the candidate's two-second
  pre-proof allowance and thirty-second expiry.
- Keep unconfirmed candidates bounded to 64 entries and thirty seconds. A
  candidate may precede the first independent proof by at most two seconds.
  Previously validated bindings are separately bounded to 64 and never carried
  into another connection or roster epoch. Conflicting names remain ambiguous.
- Invalidate pending and validated name bindings for an actor on an exact
  4236 actor,zero,flag7 entity-removal record. A subsequent local proof needs
  a new explicit name. Death flag3, another actor or connection, unknown flags,
  malformed or trailing data do not invalidate the naming evidence.
- Export bounded numeric name-source counters in local snapshots and capture
  diagnostics. They include accepted explicit headers/effects, ignored unbound
  lists, pending candidates and validated local/party names. No additional raw
  traffic or screen recording is started by these counters.

Damage calculations, class detection, party scope, login and resource baseline
rules are unchanged. A name cannot add a nearby player to the party or confirm
daily, weekly, energy or pet data. No game input or server request is automated.

## Evidence and limitation

The focused synthetic regressions cover delayed promotion, expiry, connection
and roster boundaries, unrelated member lists, conflicting explicit identities,
and preservation of damage. Offline comparison of eleven saved recordings
keeps the same damage events, totals and party participants.

The new own-equipment-screen recording contains no complete inspection reply
like the other-player control. It does contain a guild/member response, which
does not link the named persistent character to the current world actor.
Outgoing requests do not supply a decoded relationship either. Therefore this
recording still cannot name the local actor from supported evidence. A faster
parser cannot recover a relationship absent from its input.

The correction fixes a preventable delay after an explicit name has already
been validated. Universal cold-start identification remains unresolved and
requires another demonstrated source. Live validation is still pending; the
test candidate must not be published as a complete fix for that limitation.

The streaming presentation uses the same local alias while retaining the
existing names-hidden option. Only the affected presentation projection is
checked; the unchanged network/server suite is not rerun. Settings and
user data are kept outside the application binaries. Private diagnostics are
not included in this source package. This is a local source patch, not a hosted
CI run or a production release.
