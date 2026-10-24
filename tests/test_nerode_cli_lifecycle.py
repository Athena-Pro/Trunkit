"""CLI signals must cancel PostgreSQL work before the client exits."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import psycopg
import pytest

from nerode import cli


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_signal_interrupts_and_restores_handlers(monkeypatch, signum):
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def interrupted_command(_args):
        signal.raise_signal(signum)
        pytest.fail("signal did not interrupt the command")

    monkeypatch.setattr(cli, "_utf8_stdio", lambda: None)
    monkeypatch.setitem(cli._DISPATCH, "snap", interrupted_command)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--dsn", "unused", "snap"])
    assert exc.value.code == 128 + signum
    assert {sig: signal.getsignal(sig) for sig in previous} == previous


def test_handlers_restored_after_database_error(monkeypatch):
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def failed_command(_args):
        raise psycopg.OperationalError("test connection failed")

    monkeypatch.setattr(cli, "_utf8_stdio", lambda: None)
    monkeypatch.setitem(cli._DISPATCH, "snap", failed_command)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--dsn", "unused", "snap"])
    assert exc.value.code == 1
    assert {sig: signal.getsignal(sig) for sig in previous} == previous


@pytest.mark.parametrize("write", [False, True])
def test_minimizes_once_and_reports_the_persisted_automaton(monkeypatch, capsys, write):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.return_value = (7, 11) if write else (7,)
    monkeypatch.setattr(cli, "connect", lambda _dsn: conn)

    cli.cmd_min(argparse.Namespace(dsn="unused", id=3, write=write))

    assert conn.execute.call_count == 1
    expected_sql = (
        "SELECT automaton_id, claim_id FROM nerode.minimize_certified(%s)"
        if write else "SELECT nerode.minimize(%s)"
    )
    conn.execute.assert_called_once_with(expected_sql, (3,))
    assert "minimized automaton id: 7" in capsys.readouterr().out
    if write:
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()
    else:
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_signal_cancels_the_active_backend(nerode_dsn, signum):
    """Exercise psycopg's real cancellation and connection cleanup in a child.

    Windows cannot deliver POSIX process signals via os.kill, so its child
    raises the same C runtime signal after receiving the parent's trigger.
    """
    app_name = f"nerode-cancel-test-{uuid.uuid4().hex}"
    env = os.environ.copy()
    env["NERODE_DSN"] = psycopg.conninfo.make_conninfo(
        nerode_dsn, application_name=app_name,
    )
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(Path(__file__).resolve().parents[1] / "src"), env.get("PYTHONPATH"),
    )))
    child_code = f"""
import signal
import sys
import threading
from nerode import cli

def command(args):
    with cli.connect(args.dsn) as conn:
        conn.execute('SELECT count(*) FROM generate_series(1, 1000000000)')

def trigger():
    sys.stdin.readline()
    signal.raise_signal({int(signum)})

if sys.platform == 'win32':
    threading.Thread(target=trigger, daemon=True).start()
cli._DISPATCH['snap'] = command
cli.main(['snap'])
"""
    child = subprocess.Popen(
        [sys.executable, "-c", child_code], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    with psycopg.connect(nerode_dsn, autocommit=True, connect_timeout=3) as observer:
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                active = observer.execute(
                    "SELECT pid FROM pg_stat_activity "
                    "WHERE application_name = %s AND state = 'active' "
                    "AND query LIKE 'SELECT count(*) FROM generate_series%%'",
                    (app_name,),
                ).fetchone()
                if active:
                    break
                if child.poll() is not None:
                    pytest.fail(f"child exited before query: {child.communicate()}")
                time.sleep(0.05)
            else:
                pytest.fail("child did not start its query")

            if sys.platform == "win32":
                child.stdin.write("signal\n")
                child.stdin.flush()
            else:
                os.kill(child.pid, signum)

            stdout, stderr = child.communicate(timeout=15)
            assert child.returncode == 128 + signum, (stdout, stderr)
            assert "Traceback" not in stderr
            assert observer.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE application_name = %s AND state = 'active'",
                (app_name,),
            ).fetchone()[0] == 0
        finally:
            # Keep a failing regression from leaving its own runaway query.
            observer.execute(
                "SELECT pg_cancel_backend(pid) FROM pg_stat_activity "
                "WHERE application_name = %s AND state = 'active'",
                (app_name,),
            ).fetchall()
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
