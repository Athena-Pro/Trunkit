"""Generation orchestrator.

Two pipelines:

  * ``generate_pure(limit)`` — calls the spec procedure ``generate_integer_database``
    end to end. Self-contained; slow past ~10⁶.

  * ``generate_with_primesieve(limit)`` — seeds ``integers`` and ``primes`` from
    the primesieve CLI via COPY, then calls ``generate_factorizations_only``.
    Phases 2–3 are replaced by the external sieve; Phases 4–5 still run in DB.
"""

from __future__ import annotations

from psycopg import Connection

from . import primesieve


def generate_pure(conn: Connection, limit: int) -> None:
    with conn.cursor() as cur:
        cur.execute("CALL generate_integer_database(%s)", (limit,))


def generate_with_primesieve(conn: Connection, limit: int) -> None:
    _seed_integers(conn, limit)
    _seed_primes_via_copy(conn, limit)
    with conn.cursor() as cur:
        cur.execute("CALL generate_factorizations_only(%s)", (limit,))


def _seed_integers(conn: Connection, limit: int) -> None:
    """Phase 1, lifted out of PL/pgSQL — pure SQL inserts."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO integers (n, is_prime, omega, big_omega, is_squarefree)
            VALUES (1, FALSE, 0, 0, TRUE)
            ON CONFLICT DO NOTHING
            """
        )
        cur.execute(
            """
            INSERT INTO integers (n, is_prime)
            SELECT gs, FALSE FROM generate_series(2, %s) AS gs
            ON CONFLICT DO NOTHING
            """,
            (limit,),
        )


def _seed_primes_via_copy(conn: Connection, limit: int) -> None:
    """Stream primes from primesieve into ``primes`` via COPY, then flip ``is_prime``.

    COPY lands in a TEMP staging table, not ``primes`` directly: COPY has no
    ``ON CONFLICT`` clause, so copying straight into ``primes`` makes the whole
    command all-or-nothing — re-running it, or raising ``--limit`` against an
    already-populated database, dies on ``(p)=(2)`` before reaching a single new
    prime. Staging keeps COPY's speed and makes the seed incremental.

    ``discovered_order`` is deterministic (the sieve always ranks 2→1, 3→2, …),
    so a re-run agrees with the stored ranks on the overlapping prefix and only
    the new tail is inserted. The untargeted ``ON CONFLICT DO NOTHING`` covers
    both unique constraints on the table (``p`` and ``discovered_order``).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TEMP TABLE _primes_stage (
                p                BIGINT,
                discovered_order BIGINT
            ) ON COMMIT DROP
            """
        )
        with cur.copy(
            "COPY _primes_stage (p, discovered_order) FROM STDIN WITH (FORMAT TEXT)"
        ) as copy:
            for rank, p in enumerate(primesieve.iter_primes(limit), start=1):
                copy.write_row((p, rank))

        cur.execute(
            """
            INSERT INTO primes (p, discovered_order)
            SELECT p, discovered_order FROM _primes_stage
            ORDER BY discovered_order
            ON CONFLICT DO NOTHING
            """
        )

        cur.execute(
            """
            UPDATE integers SET is_prime = TRUE
            WHERE n IN (SELECT p FROM primes) AND NOT is_prime
            """
        )
