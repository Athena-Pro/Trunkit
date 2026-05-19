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
    """Stream primes from primesieve into ``primes`` via COPY, then flip ``is_prime``."""
    with conn.cursor() as cur:
        with cur.copy(
            "COPY primes (p, discovered_order) FROM STDIN WITH (FORMAT TEXT)"
        ) as copy:
            for rank, p in enumerate(primesieve.iter_primes(limit), start=1):
                copy.write_row((p, rank))

        cur.execute(
            """
            UPDATE integers SET is_prime = TRUE
            WHERE n IN (SELECT p FROM primes)
            """
        )
