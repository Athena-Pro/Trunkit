-- Unified model, step 98: the Scott / domain-theory engine.
--
-- Attests order-topological invariants of the finite posets Trunkit already
-- builds. The first target is the omega x Omega INCIDENCE poset {(i,j):i<=j}
-- whose occupied cells live in kan.bigrading_support (step 63) -- the same
-- poset whose unitriangular zeta operator the bigrading calls "Mobius /
-- inclusion-exclusion invertible". This engine upgrades that hand-wave into a
-- re-checkable lattice-structure attestation:
--
--   * Scott topology   = upper sets (Scott = Alexandrov on a finite dcpo)
--   * monotone maps     = Scott-continuous self-maps
--   * closure operators (monotone + extensive + idempotent) + their LATTICE
--   * interior operators (monotone + contractive + idempotent)
--   * the *type* of the closure lattice: Boolean 2^k / distributive / atoms
--
-- local/tools/build_scott.py enumerates these for each registered poset and
-- writes one kan.scott_lattice row. Enumeration is n^n over |P|=n, so only
-- small posets (n <= cap) are attested; larger ones are recorded as skipped.
--
-- The law-view asserts ONLY what is UNIVERSAL for every finite poset (closures
-- form a lattice; Scott recovers the order; Scott = Alexandrov). Whether the
-- closure lattice is Boolean 2^k is recorded as an attested FACT, not a
-- pass/fail law -- so a non-Boolean poset is a datum, never a refutation.
-- Idempotent.

CREATE TABLE IF NOT EXISTS kan.scott_lattice (
    poset                 TEXT PRIMARY KEY,
    carrier               TEXT NOT NULL,        -- human description of the point set
    n_points              INTEGER NOT NULL,
    n_scott_opens         INTEGER NOT NULL,     -- upper sets (Scott = Alexandrov)
    n_monotone            INTEGER,              -- Scott-continuous self-maps (n^n; NULL if uncounted)
    n_closures            INTEGER NOT NULL,     -- monotone + extensive + idempotent
    n_interiors           INTEGER NOT NULL,     -- monotone + contractive + idempotent
    n_atoms               INTEGER NOT NULL,     -- atoms of the closure lattice
    -- universal finite-poset laws (must hold):
    poset_valid           BOOLEAN NOT NULL,     -- leq is a genuine partial order
    closures_lattice      BOOLEAN NOT NULL,     -- closures form a lattice
    scott_alexandrov      BOOLEAN NOT NULL,     -- Scott-opens = upper sets
    spec_is_order         BOOLEAN NOT NULL,     -- specialization preorder = the order (T0)
    -- poset-specific facts (recorded, not asserted as laws):
    closures_distributive BOOLEAN NOT NULL,
    closures_complemented BOOLEAN NOT NULL,
    closures_boolean      BOOLEAN NOT NULL,     -- distributive AND complemented
    closures_two_pow_k    BOOLEAN NOT NULL,     -- |closures| = 2^n_atoms
    verified_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The atoms (single-cover "one-step" closures) that generate each closure
-- lattice -- the Boolean coordinates when the lattice is 2^k.
CREATE TABLE IF NOT EXISTS kan.scott_atom (
    poset   TEXT NOT NULL,
    atom_id INTEGER NOT NULL,
    moves   JSONB NOT NULL,                     -- {point: image} the atom closure applies
    PRIMARY KEY (poset, atom_id)
);

CREATE OR REPLACE VIEW kan.scott_summary AS
SELECT poset, carrier, n_points, n_scott_opens, n_closures, n_atoms,
       closures_boolean,
       (closures_boolean AND closures_two_pow_k) AS boolean_2_pow_k
  FROM kan.scott_lattice;

-- Law view (auto-discovered by cert.kan_engines_all_true). Asserts only the
-- universal laws; bool_and over an empty table returns NULL -> the engine
-- reads 'empty' (unverified), never a false refutation -- matching the
-- three-valued discipline in 79_cert_kan_engines.sql.
CREATE OR REPLACE VIEW kan.scott_laws AS
SELECT (SELECT bool_and(poset_valid)      FROM kan.scott_lattice) AS posets_valid,
       (SELECT bool_and(closures_lattice) FROM kan.scott_lattice) AS closures_form_lattice,
       (SELECT bool_and(scott_alexandrov) FROM kan.scott_lattice) AS scott_is_alexandrov,
       (SELECT bool_and(spec_is_order)    FROM kan.scott_lattice) AS scott_recovers_order,
       (SELECT count(*) FROM kan.scott_lattice)                   AS posets;
