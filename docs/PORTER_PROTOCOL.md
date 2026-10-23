# Porter v2: verified handoffs

Porter moves one session's work to the next with evidence the receiver checks
before acting. An external memory tool can contribute a payload, while the
envelope and stored closing certificate remain the handoff's authority.

This implementation delivers the foundation from the October 23, 2026 Muse
compatibility and Porter build reports. It is additive to the existing v1
Precacher API and ships with Trunkit 0.5.0. See [Muse setup](MUSE_SETUP.md) for installation,
two external PostgreSQL instances, schema initialization, and arithmetic data
generation.

## What is certified

`nerode.porter.close()` snapshots selected cache values into an append-only
`nerode.porter_envelopes` row. The typed v2 envelope carries sender and optional
receiver session IDs, creation/closure timestamps, mode, attention hint,
per-key SHA-256/byte manifests, prior-session health, and the existing certified
task-state ledger. Payload values remain in the immutable snapshot.

A content commitment is stored in the exact closing certificate's
`valid_under.trunkit.porter`; the envelope also identifies that certificate.
Open matches the stored snapshot, recomputes its manifest and envelope hash,
checks that certificate binding, replays the existing session DFA verification,
and verifies the carried ledger. Rehashing a forged envelope does not substitute
for the stored certificate. Re-closing a session appends a new snapshot and
certificate; prior snapshots retain their original payload values even if the
live sequence cache changes.

Hashes use PostgreSQL's UTF-8 JSONB text representation. They provide integrity
within the configured trusted database; they are not file signatures or an
offline, independently authenticated export format. The Trunk proof database
and Nerode workspace stay on separate PostgreSQL instances. Cross-instance
context is carried by value, without foreign keys between those instances.

## MCP workflow

Launch the installed `trunkit-mcp` console script from the MCP client. Pass
`CALX_DSN` and `NERODE_DSN` in its environment. Consumers can leave writes
disabled. To create handoffs, claim/release work, or record gate decisions, set
`TRUNKIT_ALLOW_WRITE=1` before starting the server.

The examples below are tool argument objects. Fields ending in `_json` contain
JSON strings, as required by their MCP schemas.

1. Call `porter_close`:

```json
{
  "session_id": "sender-001",
  "payloads_json": "{\"task\":{\"objective\":\"Review the release\"}}",
  "ledger_entries_json": "[{\"path\":\"reservation\",\"value\":{\"permitted\":false},\"schema_type\":\"reservation\"}]",
  "attention_hint": "Review the task and reservation before acting",
  "mode": "reopenable"
}
```

Store the returned envelope. Payload storage, typed-ledger absorption, closing
certificates, and snapshot creation commit in one transaction. Invalid options
or a manifest budget failure roll back the whole operation.

2. Call `porter_open` with `envelope_json` containing that serialized envelope.
The default returns certificate status, manifest, ledger-valid flag, and health,
with `resolved: {}`. This transaction is read-only and records no receipt.
To inline values in the same call, also send `payload_keys_json: "[\"task\"]"`.

3. Before the receiver's first environment-changing action, call
`porter_open_and_gate` with the serialized `envelope_json`,
`receiver_session: "receiver-001"`, `tool` and `args_json: "{}"`. It restores the
verified ledger and records a replayable `ALLOW`, `REVISE`, or `BLOCK` decision.
The caller should proceed only on `ALLOW`.

Policies must be registered for the relevant tools. The existing gate allows
unregistered tools; registration makes a tool participate in policy checks.
For example, configure a reservation rule through public SQL during setup:

```sql
SELECT nerode.register_policy(
  'release-reservation', 'publish_release',
  '($1->''reservation''->>''permitted'')::boolean',
  'Publishing needs an affirmative reservation', 'block'
);
```

With the example ledger above, `tool: "publish_release"` returns `BLOCK`.
An unknown reservation returns `REVISE`. Gate replay remains available through
`nerode.replay_gate()` and the existing decision certificate.

Changing an envelope field, carried ledger entry, manifest hash, or certificate
binding makes open fail closed without returning payload values. An unsupported
version returns an explicit error. Legacy v1 envelopes still use
`nerode.precache.Precacher.open()`; they are not silently upgraded to v2.

## Budgets and claim leases

`max_envelope_bytes` defaults to 65,536 and permits 1,024 through 1,048,576 bytes.
The bound covers the whole v2 envelope, including its manifest and carried ledger.
Selected payloads use a separate `max_payload_bytes` budget, defaulting to
65,536, with a permitted range of 0 through 1,048,576. A selection that exceeds
the budget returns no payload values and sets `payload_budget_exceeded: true`;
values are never truncated. A single open can inline every requested key that
fits, without a separate tool call per payload.

`mode: "reopenable"` is the default. `mode: "claim_once"` enables one active
lease at a time. Call `porter_claim` with `envelope_json`, `receiver_session`,
and optional `lease_seconds` (default 300, range 1 through 86,400). An atomic
insert/update gives simultaneous contenders exactly one current claim token.
Pass that token to `porter_open` when requesting payloads and to
`porter_open_and_gate`. `porter_release` requires the current unexpired token;
stale tokens cannot release a newer lease. An expired or released lease can
be claimed again, so callers still need idempotent external effects.

Manifest-only audit opens remain repeatable while a lease is held. A directed
envelope requires its `intended_receiver` to match `receiver_session`. These
are session-routing and lease controls, not a multi-user authentication layer.

## Health and exactness

Close snapshots accepting session-loop detectors and cybernetic warnings for
dead time, repeated metric rise, oscillation, and homeostasis alarms. The health
section is committed with the envelope and returned on open, including a clean
empty warning list when no pattern matches. Health observations describe the
prior trace; the gate independently checks the proposed next action.

No LLM summary, automatic capture, or heuristic consolidation is introduced by
this protocol. Existing exactness rules and three-valued certificate behavior
remain in force. A verified handoff does not turn arbitrary payload prose into
a mathematical theorem.

## Report implementation map

| Build report phase | Delivered here | Remaining scope |
| --- | --- | --- |
| 0: harden core | Live ledger/gate/tamper tests; typed restore guard; snapshot lock; stored export hashes preserved; explicit empty exports | Base schema still has no hash-chain storage extension; absent chain evidence is unverified |
| 1: protocol v2 | Typed envelopes, certificate-bound immutable snapshots, per-key manifests, directed routing, budgets, atomic lease/expiry/release | Multi-user identity is separate from receiver routing |
| 2: capture | Existing deliberate capture remains the default | Opt-in harness adapters, sanitization rules, redaction commitments |
| 3: retrieval | Attention hints and manifest inspection | Indexed search and rebuildable derived briefs |
| 4: aging | Snapshot history is append-only | Retention tiers, compaction/restore, duplicate and contradiction flags |
| 5: reach | Five Porter MCP tools with write gating; real stdio acceptance test | Signed cross-database file transport, two external harness adapters, user namespaces and audit log |
| 6: gating/health | Verified open-and-gate through MCP; prior-session DFA health | Broader harness policy wiring and additional composite detectors |

Neither capture/retention nor cryptographic signing is implied by this release.
Those features need their own acceptance tests before being marked complete.

## Acceptance tests

Use dedicated disposable PostgreSQL instances and set `CALX_TEST_DSN` and
`NERODE_TEST_DSN`. The test fixtures refuse to use default production databases.

```sh
python -m pytest tests/test_porter_protocol.py tests/test_porter_security.py \
  tests/test_porter_delivery.py tests/test_porter_mcp.py tests/test_quickstart.py -q
```

The suite checks ledger carry and replayable gate decisions, tampered values
and hashes, preserved payload snapshots, concurrent claims, expiry/release and
stale tokens, byte budgets, receiver routing, read-only opens, dead-time health,
write gating, and a full real-stdio MCP handoff. The installed-wheel Muse CI
separately verifies clean dependency installation and the optional JSON-string
arguments that failed in the report.


For a newcomer-facing demonstration against an initialized disposable Nerode
database, run the installed wheel's interpreter from the source checkout:

```sh
export TRUNKIT_ALLOW_WRITE=1
.venv/bin/python tools/demo_porter_mcp.py --write
```

The demo uses public SQL only to seed a policy and a dead-time trace. The entire
handoff then uses the installed MCP console script: close, manifest open, claim,
selected payload, budget rejection, tamper rejection, carried-ledger BLOCK and
release, with the prior health warning visible. It appends uniquely named demo
rows. The Muse CI job runs the same demonstration against its disposable service.


Local acceptance: 1,497 tests passed (3 skipped, 27 excluded by marker), with
Python 3.12, MCP 1.30.0 and PostgreSQL 17.9 on Windows. The clean installed-wheel
MCP demo also passed. Ubuntu 24.04 / PostgreSQL 16 acceptance is configured in
CI; it has not been run locally.
