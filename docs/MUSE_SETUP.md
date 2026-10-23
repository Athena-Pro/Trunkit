# Muse and other agent VMs

Trunkit supports a local stdio MCP process and externally supplied PostgreSQL.
Docker is optional. Use Python 3.11 or newer; the Muse report exercised Ubuntu
24.04 and Python 3.12. These instructions target Trunkit 0.5.0 and newer.

## Install before running a command

System Python can be externally managed (PEP 668). Create a venv rather than
installing into it:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade 'trunkit[muse]'
trunkit --help
nerode --help
```

From a source checkout, install with `python3 -m pip install '.[muse]'`.
For a built release wheel, replace the install command with:

```sh
python3 -m pip install './dist/trunkit-0.5.0-py3-none-any.whl[muse]'
```

The `muse` extra includes both the binary Psycopg implementation (bundled libpq)
and the supported MCP SDK. No PostgreSQL client library needs to be installed
on the agent's host. The base installation deliberately keeps pure-Python
Psycopg so Termux and platforms without binary wheels remain supported; that
base route requires a working system libpq. `.[binary,mcp]` is equivalent to
`.[muse]` for this setup.

The MCP dependency is `mcp>=1.30,<2`. Trunkit uses FastMCP's 1.x API; do not
upgrade that environment independently to MCP 2.x. The floor includes the
upstream Settings model fix, so the old `pydantic-settings<2.13` workaround is
unnecessary. A fresh wheel install was exercised with MCP 1.30.0, Pydantic
2.14.1 and pydantic-settings 2.16.1. See the upstream
[v1 release notes](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.29.1)
and [v1 documentation](https://py.sdk.modelcontextprotocol.io/v1/).

The distribution is named `trunkit`. Its importable Python packages are `calx`,
`nerode`, and `trunkit_mcp`; there is no `trunkit` module or `python -m trunkit`
entry point. Use the installed `trunkit`, `nerode`, and `trunkit-mcp` commands.

## Connect to PostgreSQL without Docker

Supply PostgreSQL 16 databases and credentials with permission to apply the
Trunkit schemas. They can be on a remote server or a native server provided by
the environment. Use separate PostgreSQL instances to keep the arithmetic
ledger and Nerode / Porter workspace isolated. Two databases on the same
PostgreSQL backend do not provide the intended trust separation. The following
example assumes those databases and users already exist; replace the example passwords and endpoints:

```sh
export CALX_DSN='postgresql://trunk:replace-me@ledger-host:5432/trunk'
export NERODE_DSN='postgresql://nerode:replace-me@workspace-host:5432/nerode'
trunkit quickstart --no-docker
```

`--no-docker` requires explicit connections for both databases, applies their
schemas, and creates no compose file. It never starts or installs a server.
Connection flags are available when environment variables are inconvenient:

```sh
trunkit --dsn "$CALX_DSN" quickstart --no-docker --nerode-dsn "$NERODE_DSN"
```

For separate migration steps, the equivalent commands are:

```sh
trunkit --dsn "$CALX_DSN" init
nerode --dsn "$NERODE_DSN" close --apply
```

A native PostgreSQL process must run as an unprivileged OS user. A root agent
cannot run PostgreSQL directly. The server binaries, all parent directories,
and data directory must be accessible to that user; an agent's private home
or workspace may prevent this even when the data directory itself has suitable
permissions. Put native PostgreSQL under an administrator-provided accessible
location (for example `/opt` for binaries and `/var/lib` for data), or use an
external database. If the VM forbids changing directory ownership or switching
users, use the external route above. A Python package containing PostgreSQL
binaries does not remove PostgreSQL's OS-user and filesystem requirements.

The arithmetic CLI defaults to the native ledger at
`postgresql://trunk:trunk@localhost:5432/trunk`. The Docker quickstart instead
uses port 5434 for that ledger. Nerode defaults to
`postgresql://nerode:nerode@localhost:5435/nerode`. The no-Docker route requires
explicit connections to avoid silently choosing the wrong instances.

## Generate data before checking arithmetic claims

Schema initialization seeds claims and verification methods. Arithmetic claim
verification also needs integer data covering the claim's inputs:

```sh
trunkit generate --limit 10000 --backend pure
trunkit verify 2
```

For the seeded claim `aliquot_step(28) = 28`, a schema-only database cannot
supply the result. Generate data first; a missing result is `unverified` and is
not a proof that the claim is false. The `pure` backend requires no external
prime-sieve program. Choose a larger limit for claims about larger integers.

Verification does not record a certificate. Record one explicitly before
exporting a certified bundle:

```sh
trunkit check 2 --write
trunkit export 2 > claim-2.json
trunkit verify --bundle claim-2.json
python3 -m calx.smoke
```

`calx.smoke` checks both configured databases and rolls its checks back. The
base schema exports a version 1 bundle with the recorded certificate and SQL
probe. It does not store certificate hash-chain columns: `ledger_chain` reports
`unverified` with `chain_ok: null`, while live `bundle_verify` can still replay
and validate the SQL computation. If an installed extension stores original
row hashes, version 2 exports preserve those stored hashes and can establish
chain integrity. The exporter must never invent a stored hash by recomputing
one at export time. A bundle without a recorded certificate cannot establish
that the claim has been certified. Offline structural checks alone
cannot replay a SQL computation; live SQL claim verification requires the
configured arithmetic database and generated data.

## Spawn the MCP server over stdio

Configure the MCP client to launch the absolute path to the venv's
`trunkit-mcp` executable. On Linux it is `.venv/bin/trunkit-mcp`; on Windows it
is `.venv\Scripts\trunkit-mcp.exe`. Pass `CALX_DSN` and `NERODE_DSN` in the
child process environment. A stdio client starts the process itself; there is
no daemon to install or HTTP port to expose.

Keep `TRUNKIT_ALLOW_WRITE` unset (or `0`) for consumers. To enable prover calls,
the process owner must explicitly set `TRUNKIT_ALLOW_WRITE=1` before the server
starts. CLI recording separately requires `--write`. Do not set `TRUNKIT_SRC`
for a wheel installation; that development override can load an unrelated
checkout instead of the installed release.

`kernel_verify`, recurrence / morphism verification, `commitment_verify`, and
`arith_verify` work without PostgreSQL. JSON-string parameters must be sent as
strings containing JSON. For example `interp_json` is the string `"{}"`, and
`leaves_json` is the string `'["alpha","beta"]'`. Optional JSON strings may be
omitted; FastMCP must not receive a list or dict in their place. Tool discovery
is dynamic: newer releases can expose more tools than the 13 in the original
0.4.0 report.

## Reproduce the wheel acceptance check

The repository's `tools/smoke_muse.py` uses the installed console scripts and a
real MCP stdio client. Run it with the clean venv's interpreter after installing
only `wheel[muse]`; do not install dev dependencies in that venv:

```sh
.venv/bin/python tools/smoke_muse.py
# After schema initialization, generate, and check 2 --write on dedicated DBs:
.venv/bin/python tools/smoke_muse.py --database
```

It checks CLI help, wheel import locations, bundled libpq, tool discovery,
factorization verification, both optional commitment JSON arguments (including
a tampered commitment), and an explicit arithmetic interpretation. Database
mode additionally checks standing, generated claim verification, bundle export,
the chain verdict appropriate to the bundle version, and live bundle replay. It performs consumer calls; the setup
commands above are the explicit writes.

The Muse CI job builds separately and installs the wheel into a new venv in
Ubuntu 24.04 with externally managed Python, no system libpq and no Docker
binary. PostgreSQL is supplied as a CI service and accessed only by DSN. It
runs both smoke modes and the installed arithmetic / Porter checks. The CI
service uses the runner's container infrastructure; Trunkit's bootstrap and
agent environment do not require a container runtime.


Local acceptance on October 23, 2026 used Windows, Python 3.12 and two isolated
PostgreSQL 17.9 instances. The broader suite passed 1,497 tests with 3 skips and
27 marker exclusions. Both clean-wheel smoke modes and the installed-wheel
Porter v2 MCP demonstration passed. The Ubuntu 24.04 / PostgreSQL 16 workflow
is configured for CI and was not executed locally.
