"""
tests/test_categorical.py
=========================
Tests for nerode.sql/80_categorical.sql.

Covers:
  - quotient_maps view (epimorphisms that reduce state count)
  - check_triangle_commutes
  - product_universal_property
  - calx_functor_report
  - categorical_profile
"""

from __future__ import annotations

import pytest
import psycopg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh(dsn: str) -> psycopg.Connection:
    """Autocommit connection for reading data written outside the test fixture."""
    return psycopg.connect(dsn, autocommit=True)


def _corpus_id(conn, slug: str) -> int:
    row = conn.execute(
        "SELECT automaton_id FROM nerode.corpus WHERE slug = %s", (slug,)
    ).fetchone()
    assert row, f"corpus slug {slug!r} not found"
    return row[0]


def _product_id(conn, lhs_slug: str, rhs_slug: str) -> int:
    row = conn.execute(
        "SELECT product_id FROM nerode.product_pairs WHERE lhs_slug=%s AND rhs_slug=%s",
        (lhs_slug, rhs_slug),
    ).fetchone()
    assert row, f"product pair {lhs_slug}x{rhs_slug} not found"
    return row[0]


def _morphism_id(conn, src_id: int, tgt_id: int) -> int | None:
    row = conn.execute(
        "SELECT id FROM nerode.morphisms WHERE src_id=%s AND tgt_id=%s LIMIT 1",
        (src_id, tgt_id),
    ).fetchone()
    return row[0] if row else None


def _ensure_morphism(conn, src_id: int, tgt_id: int) -> int:
    mid = _morphism_id(conn, src_id, tgt_id)
    if mid is None:
        mid = conn.execute(
            "SELECT nerode.register_morphism(%s, %s)", (src_id, tgt_id)
        ).fetchone()[0]
        assert mid is not None, f"no morphism from {src_id} to {tgt_id}"
    return mid


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ids(nerode_dsn):
    """Return a dict of known automaton IDs from the corpus."""
    with _fresh(nerode_dsn) as c:
        return {
            "c4":  _corpus_id(c, "cycle_4"),
            "c6":  _corpus_id(c, "cycle_6"),
            "c9":  _corpus_id(c, "cycle_9"),
            "c10": _corpus_id(c, "cycle_10"),
            "p46": _product_id(c, "cycle_4", "cycle_6"),
            "p49": _product_id(c, "cycle_4", "cycle_9"),
            "p4t": _product_id(c, "cycle_4", "cycle_10"),
            "p69": _product_id(c, "cycle_6", "cycle_9"),
            "p6t": _product_id(c, "cycle_6", "cycle_10"),
        }


# ---------------------------------------------------------------------------
# TestQuotientMapsView
# ---------------------------------------------------------------------------


class TestQuotientMapsView:
    """nerode.quotient_maps VIEW."""

    def test_view_returns_rows(self, nerode_dsn, ids):
        with _fresh(nerode_dsn) as c:
            rows = c.execute("SELECT * FROM nerode.quotient_maps").fetchall()
        assert len(rows) > 0

    def test_all_are_epimorphisms(self, nerode_dsn):
        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT kind FROM nerode.quotient_maps"
            ).fetchall()
        for r in rows:
            assert r[0] == "epimorphism"

    def test_all_reduce_state_count(self, nerode_dsn):
        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT src_states, tgt_states FROM nerode.quotient_maps"
            ).fetchall()
        for src_st, tgt_st in rows:
            assert tgt_st < src_st

    def test_states_collapsed_positive(self, nerode_dsn):
        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT states_collapsed FROM nerode.quotient_maps"
            ).fetchall()
        for (collapsed,) in rows:
            assert collapsed > 0

    def test_corpus_eigenforms_not_in_view(self, nerode_dsn, ids):
        """Minimal corpus DFAs have no outgoing quotient maps."""
        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT src_id FROM nerode.quotient_maps"
            ).fetchall()
        src_ids = {r[0] for r in rows}
        for slug_key in ("c4", "c6", "c9", "c10"):
            assert ids[slug_key] not in src_ids, (
                f"corpus eigenform {slug_key} should not appear as quotient source"
            )


# ---------------------------------------------------------------------------
# TestCheckTriangleCommutes
# ---------------------------------------------------------------------------


class TestCheckTriangleCommutes:
    """nerode.check_triangle_commutes(f, g, h)."""

    @pytest.fixture(scope="class")
    def triangle_ids(self, nerode_dsn, ids):
        """Ensure P49→P46 morphism exists, collect IDs for triangle checks."""
        with _fresh(nerode_dsn) as c:
            # π₁: P46→c4  π₂: P46→c6
            pi1 = _ensure_morphism(c, ids["p46"], ids["c4"])
            pi2 = _ensure_morphism(c, ids["p46"], ids["c6"])
            # φ₄: P49→c4  φ₉: P49→c9
            phi4 = _ensure_morphism(c, ids["p49"], ids["c4"])
            # P49→c6: 6|36, so this morphism exists
            phi6 = _ensure_morphism(c, ids["p49"], ids["c6"])
            # P49→P46: 12|36
            h_49_46 = _ensure_morphism(c, ids["p49"], ids["p46"])
        return {
            "pi1": pi1, "pi2": pi2,
            "phi4": phi4, "phi6": phi6,
            "h": h_49_46,
        }

    def test_left_triangle_commutes(self, nerode_dsn, triangle_ids):
        """(P49→P46) ; (P46→c4) = (P49→c4) must commute."""
        with _fresh(nerode_dsn) as c:
            result = c.execute(
                "SELECT nerode.check_triangle_commutes(%s, %s, %s)",
                (triangle_ids["h"], triangle_ids["pi1"], triangle_ids["phi4"]),
            ).fetchone()[0]
        assert result is True

    def test_right_triangle_commutes(self, nerode_dsn, triangle_ids):
        """(P49→P46) ; (P46→c6) = (P49→c6) must commute."""
        with _fresh(nerode_dsn) as c:
            result = c.execute(
                "SELECT nerode.check_triangle_commutes(%s, %s, %s)",
                (triangle_ids["h"], triangle_ids["pi2"], triangle_ids["phi6"]),
            ).fetchone()[0]
        assert result is True

    def test_wrong_third_morphism_does_not_commute(self, nerode_dsn, ids, triangle_ids):
        """Using the wrong codomain morphism must not commute."""
        with _fresh(nerode_dsn) as c:
            # P49→c9 morphism
            phi9 = _ensure_morphism(c, ids["p49"], ids["c9"])
            # (P49→P46) ; (P46→c4) should NOT equal (P49→c9)
            result = c.execute(
                "SELECT nerode.check_triangle_commutes(%s, %s, %s)",
                (triangle_ids["h"], triangle_ids["pi1"], phi9),
            ).fetchone()[0]
        assert result is False

    def test_missing_morphism_returns_null(self, nerode_dsn):
        """Non-existent morphism ID → NULL result."""
        with _fresh(nerode_dsn) as c:
            result = c.execute(
                "SELECT nerode.check_triangle_commutes(%s, %s, %s)",
                (999999, 999998, 999997),
            ).fetchone()[0]
        assert result is None


# ---------------------------------------------------------------------------
# TestProductUniversalProperty
# ---------------------------------------------------------------------------


class TestProductUniversalProperty:
    """nerode.product_universal_property(product, lhs, rhs, witness)."""

    def test_self_witness_holds(self, nerode_dsn, ids):
        """P46 as its own witness: mediating map is the identity."""
        with _fresh(nerode_dsn) as c:
            _ensure_morphism(c, ids["p46"], ids["c4"])
            _ensure_morphism(c, ids["p46"], ids["c6"])
            result = c.execute(
                "SELECT nerode.product_universal_property(%s,%s,%s,%s)",
                (ids["p46"], ids["c4"], ids["c6"], ids["p46"]),
            ).fetchone()[0]
        assert result["universal_holds"] is True
        assert result["mediating_map_size"] == 12  # |P46| = lcm(4,6) = 12

    def test_self_witness_identity_map(self, nerode_dsn, ids):
        """Mediating map for self-witness must be the identity function."""
        with _fresh(nerode_dsn) as c:
            _ensure_morphism(c, ids["p46"], ids["c4"])
            _ensure_morphism(c, ids["p46"], ids["c6"])
            result = c.execute(
                "SELECT nerode.product_universal_property(%s,%s,%s,%s)",
                (ids["p46"], ids["c4"], ids["c6"], ids["p46"]),
            ).fetchone()[0]
        h = result["mediating_map"]
        assert all(str(h[k]) == str(k) for k in h)

    def test_external_witness_holds(self, nerode_dsn, ids):
        """P49 (36 states) as witness for P46 (12 states): mediating map has 36 entries."""
        with _fresh(nerode_dsn) as c:
            _ensure_morphism(c, ids["p46"], ids["c4"])
            _ensure_morphism(c, ids["p46"], ids["c6"])
            _ensure_morphism(c, ids["p49"], ids["c4"])
            _ensure_morphism(c, ids["p49"], ids["c6"])
            result = c.execute(
                "SELECT nerode.product_universal_property(%s,%s,%s,%s)",
                (ids["p46"], ids["c4"], ids["c6"], ids["p49"]),
            ).fetchone()[0]
        assert result["universal_holds"] is True
        assert result["mediating_map_size"] == 36  # |P49| = lcm(4,9) = 36

    def test_external_witness_map_consistent_with_registered(self, nerode_dsn, ids):
        """Mediating map from universal property == state_map of registered morphism."""
        with _fresh(nerode_dsn) as c:
            _ensure_morphism(c, ids["p46"], ids["c4"])
            _ensure_morphism(c, ids["p46"], ids["c6"])
            _ensure_morphism(c, ids["p49"], ids["c4"])
            _ensure_morphism(c, ids["p49"], ids["c6"])
            h_id = _ensure_morphism(c, ids["p49"], ids["p46"])
            result = c.execute(
                "SELECT nerode.product_universal_property(%s,%s,%s,%s)",
                (ids["p46"], ids["c4"], ids["c6"], ids["p49"]),
            ).fetchone()[0]
            reg_map = c.execute(
                "SELECT state_map FROM nerode.morphisms WHERE id=%s", (h_id,)
            ).fetchone()[0]
        h = result["mediating_map"]
        assert all(str(h.get(k)) == str(reg_map.get(k)) for k in reg_map)

    def test_missing_projection_returns_error(self, nerode_dsn, ids):
        """Asking for a product pair with no registered projections returns an error JSONB."""
        with _fresh(nerode_dsn) as c:
            # Use c4 and c6 as "product" and "lhs/rhs" — no projection morphisms exist
            result = c.execute(
                "SELECT nerode.product_universal_property(%s,%s,%s,%s)",
                (ids["c4"], ids["c6"], ids["c9"], ids["c4"]),
            ).fetchone()[0]
        assert "error" in result


# ---------------------------------------------------------------------------
# TestCalxFunctorReport
# ---------------------------------------------------------------------------


class TestCalxFunctorReport:
    """nerode.calx_functor_report()."""

    @pytest.fixture(scope="class")
    def report(self, nerode_dsn):
        with _fresh(nerode_dsn) as c:
            return c.execute("SELECT * FROM nerode.calx_functor_report()").fetchall()

    def test_returns_rows(self, report):
        assert len(report) > 0

    def test_all_epimorphisms(self, report):
        """All corpus-registered morphisms are epimorphisms."""
        for row in report:
            assert row[1] == "epimorphism"

    def test_tgt_divides_src_for_cycle_morphisms(self, report):
        """For cycle product→factor morphisms, |tgt| always divides |src|."""
        for row in report:
            mid, kind, src_id, src_st, tgt_id, tgt_st, ratio, div, cert = row
            assert div is True, (
                f"morphism {mid}: |tgt|={tgt_st} does not divide |src|={src_st}"
            )

    def test_ratio_at_least_one(self, report):
        """State ratio |src|/|tgt| ≥ 1 for all epimorphisms."""
        for row in report:
            ratio = float(row[6])
            assert ratio >= 1.0

    def test_cert_claim_ids_positive(self, report):
        for row in report:
            assert row[8] > 0


# ---------------------------------------------------------------------------
# TestCategoricalProfile
# ---------------------------------------------------------------------------


class TestCategoricalProfile:
    """nerode.categorical_profile(automaton_id)."""

    def test_eigenform_flag_corpus(self, nerode_dsn, ids):
        """All minimal corpus DFAs are eigenforms."""
        with _fresh(nerode_dsn) as c:
            for key in ("c4", "c6", "c9", "c10"):
                profile = c.execute(
                    "SELECT nerode.categorical_profile(%s)", (ids[key],)
                ).fetchone()[0]
                assert profile["is_eigenform"] is True, f"{key} should be eigenform"

    def test_eigenform_flag_products(self, nerode_dsn, ids):
        """Product DFAs (minimal by construction) are eigenforms."""
        with _fresh(nerode_dsn) as c:
            for key in ("p46", "p49", "p4t", "p69", "p6t"):
                profile = c.execute(
                    "SELECT nerode.categorical_profile(%s)", (ids[key],)
                ).fetchone()[0]
                assert profile["is_eigenform"] is True, f"{key} product should be eigenform"

    def test_state_count_correct(self, nerode_dsn, ids):
        """State count in profile matches known values."""
        expected = {
            "c4": 4, "c6": 6, "c9": 9, "c10": 10,
            "p46": 12, "p49": 36, "p4t": 20, "p69": 18, "p6t": 30,
        }
        with _fresh(nerode_dsn) as c:
            for key, exp_count in expected.items():
                profile = c.execute(
                    "SELECT nerode.categorical_profile(%s)", (ids[key],)
                ).fetchone()[0]
                assert profile["state_count"] == exp_count, (
                    f"{key}: expected {exp_count}, got {profile['state_count']}"
                )

    def test_corpus_automata_have_incoming_morphisms(self, nerode_dsn, ids):
        """Factor DFAs (cycle_N) have incoming morphisms from products."""
        with _fresh(nerode_dsn) as c:
            for key in ("c4", "c6", "c9", "c10"):
                profile = c.execute(
                    "SELECT nerode.categorical_profile(%s)", (ids[key],)
                ).fetchone()[0]
                assert profile["morphisms_in_count"] >= 2, (
                    f"{key} should have at least 2 incoming morphisms"
                )

    def test_corpus_automata_are_terminal_object_candidates(self, nerode_dsn, ids):
        """Factor DFAs with incoming morphisms classify as terminal_object_candidate."""
        with _fresh(nerode_dsn) as c:
            for key in ("c4", "c6", "c9", "c10"):
                profile = c.execute(
                    "SELECT nerode.categorical_profile(%s)", (ids[key],)
                ).fetchone()[0]
                assert profile["categorical_role"] == "terminal_object_candidate"

    def test_product_automata_are_source_objects(self, nerode_dsn, ids):
        """Product DFAs classify as eigenform, source_object, or terminal_object_candidate
        depending on which morphisms have been registered; never 'isolated'."""
        valid_roles = {"eigenform", "source_object", "terminal_object_candidate"}
        with _fresh(nerode_dsn) as c:
            for key in ("p46", "p49", "p4t", "p69", "p6t"):
                profile = c.execute(
                    "SELECT nerode.categorical_profile(%s)", (ids[key],)
                ).fetchone()[0]
                assert profile["categorical_role"] in valid_roles, (
                    f"{key} role={profile['categorical_role']!r} not in {valid_roles}"
                )

    def test_calx_present(self, nerode_dsn, ids):
        """Calx facts block is always embedded in the profile."""
        with _fresh(nerode_dsn) as c:
            profile = c.execute(
                "SELECT nerode.categorical_profile(%s)", (ids["c4"],)
            ).fetchone()[0]
        assert "calx" in profile
        assert "calx_available" in profile["calx"]
        assert profile["calx"]["state_count"] == 4

    def test_calx_signature_cycle4(self, nerode_dsn, ids):
        """cycle_4 has prime signature 2^2 when calx is available."""
        with _fresh(nerode_dsn) as c:
            profile = c.execute(
                "SELECT nerode.categorical_profile(%s)", (ids["c4"],)
            ).fetchone()[0]
        if not profile["calx"]["calx_available"]:
            pytest.skip("calx schema not available on this DB")
        sig = profile["calx"]["calx_facts"]["signature"]
        assert sig == "2^2"

    def test_calx_signature_product_is_union(self, nerode_dsn, ids):
        """Product p46 = cycle_4 x cycle_6 has signature 2^2 * 3 (multiset union)."""
        with _fresh(nerode_dsn) as c:
            profile = c.execute(
                "SELECT nerode.categorical_profile(%s)", (ids["p46"],)
            ).fetchone()[0]
        if not profile["calx"]["calx_available"]:
            pytest.skip("calx schema not available on this DB")
        # 12 = 2^2 * 3
        sig = profile["calx"]["calx_facts"]["signature"]
        assert "2" in sig and "3" in sig

    def test_not_found_returns_error(self, nerode_dsn):
        """Non-existent ID returns an error JSONB."""
        with _fresh(nerode_dsn) as c:
            profile = c.execute(
                "SELECT nerode.categorical_profile(%s)", (999999,)
            ).fetchone()[0]
        assert "error" in profile
