-- Unified model, step 68: attest the lithon integration (F_1 / Spec(Z)).
--
-- lithon (tel/lithon) integrated as a CONCRETE SPLITTING of the value map:
-- kan category 'lithon' with val:lithon->seq (Phi) and pack:seq->lithon
-- (state_from_integer). Five laws (hash-pinned self-contained checker
-- proofs/lithon.py):
--   P1 retraction      Phi(pack(n)) = n on every reachable n (val o pack=id);
--   P2 W1 single-atom  an in-window prime power p^k (p among the first 15
--                      primes, exponent k<=16, value<=MAX) packs to the
--                      single cell (pi(p), k-1); grid row index = pi(p) =
--                      ht(p^k) -- lithon realises the chromatic/prime_members
--                      data geometrically within its 2-D adelic horizon;
--   P3a F_1 adjoins 1  the unit 1 is unreachable from the prime rows alone
--                      (smallest prime atom = 2) but reachable with row-0:
--                      F_1 literally adjoins the multiplicative unit to
--                      Spec(Z);
--   P3b F_1 load-bearing  row-0 is used by some pack(n);
--   P3c row-0 == W_0   omega(0)=omega(1)=0, so the F_1 row is exactly the
--                      W_0 units rung the identity-decomposition capstone
--                      required -- "F_1 glued to Spec(Z)" == "W_0 glued to
--                      the prime tower".
-- Canonical: in-window prime-power grid map, 122 entries (sha 1424c59096ea8fee).
--
-- Live: kan carries category 'lithon' + functors val/pack, tables
-- kan.lithon/_witness (views *_summary/_ht_correspondence/_laws).
-- Driven by tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'lithon',
       '{"functors":{"val":"lithon->seq (Phi)","pack":"seq->lithon (state_from_integer)"},
         "laws":["P1_retraction","P2_W1_single_atom","P3a_F1_adjoins_unit",
                 "P3b_F1_load_bearing","P3c_row0=W0"],
         "horizon":"first 15 primes x exponent<=16 (2-D adelic window)",
         "thesis":"F_1 (row-0) glued to Spec(Z) == W_0 units rung glued to the prime tower",
         "canonical":"in-window prime-power grid map, 122 entries, sha 1424c59096ea8fee"}'::jsonb,
       'lithon is a concrete splitting of the value map: within its 15-prime adelic horizon it realises the chromatic ht / prime-power data exactly, and F_1 (row-0) glues the unit to Spec(Z) -- the same W_0 the identity capstone required',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'lithon is a concrete splitting of the value map: within its 15-prime adelic horizon it realises the chromatic ht / prime-power data exactly, and F_1 (row-0) glues the unit to Spec(Z) -- the same W_0 the identity capstone required'
);
