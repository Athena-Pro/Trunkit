"""Bootstrap routing must never fall back to the native proof ledger."""

from argparse import Namespace
from unittest.mock import Mock

import pytest

from calx import quickstart
from calx.cli import build_parser


@pytest.fixture
def bootstrap(monkeypatch, tmp_path):
    monkeypatch.delenv("CALX_DSN", raising=False)
    monkeypatch.delenv("NERODE_DSN", raising=False)
    start = Mock(return_value=True)
    wait = Mock(return_value=True)
    apply = Mock()
    monkeypatch.setattr(quickstart, "start_databases", start)
    monkeypatch.setattr(quickstart, "wait_for", wait)
    monkeypatch.setattr(quickstart, "apply_schemas", apply)
    args = Namespace(dir=str(tmp_path), dsn=None, nerode_dsn=None, compose_only=False)
    return args, start, wait, apply


def test_generated_defaults_are_used_for_readiness_schema_and_next_commands(bootstrap, capsys):
    args, start, wait, apply = bootstrap
    assert quickstart.run(args) == 0
    assert start.call_args.args[1] == ["db-trunkit", "db-nerode"]
    assert [call.args[0] for call in wait.call_args_list] == [
        quickstart.TRUNK_DSN, quickstart.NERODE_DSN,
    ]
    apply.assert_called_once_with(quickstart.TRUNK_DSN, quickstart.NERODE_DSN)
    output = capsys.readouterr().out
    assert f"trunkit --dsn {quickstart.TRUNK_DSN} generate" in output
    assert f"nerode --dsn {quickstart.NERODE_DSN} build" in output


def test_existing_environment_targets_do_not_require_docker(bootstrap, monkeypatch):
    args, start, wait, apply = bootstrap
    monkeypatch.setenv("CALX_DSN", "postgresql://trunk@localhost:6544/test")
    monkeypatch.setenv("NERODE_DSN", "postgresql://nerode@localhost:6545/test")
    assert quickstart.run(args) == 0
    start.assert_not_called()
    apply.assert_called_once_with(
        "postgresql://trunk@localhost:6544/test", "postgresql://nerode@localhost:6545/test",
    )
    assert [call.args[0] for call in wait.call_args_list] == list(apply.call_args.args)


def test_explicit_connections_override_environment(bootstrap, monkeypatch):
    args, start, _, apply = bootstrap
    args.dsn = "postgresql://trunk@localhost:7544/explicit"
    args.nerode_dsn = "postgresql://nerode@localhost:7545/explicit"
    monkeypatch.setenv("CALX_DSN", "unused-trunk")
    monkeypatch.setenv("NERODE_DSN", "unused-nerode")
    assert quickstart.run(args) == 0
    start.assert_not_called()
    apply.assert_called_once_with(args.dsn, args.nerode_dsn)


@pytest.mark.parametrize("configured,service", [
    ("CALX_DSN", "db-nerode"), ("NERODE_DSN", "db-trunkit"),
])
def test_only_unconfigured_database_is_started(bootstrap, monkeypatch, configured, service):
    args, start, _, _ = bootstrap
    monkeypatch.setenv(configured, "postgresql://localhost:6543/existing")
    assert quickstart.run(args) == 0
    assert start.call_args.args[1] == [service]


def test_unreachable_target_does_not_apply_either_schema(bootstrap):
    args, _, wait, apply = bootstrap
    wait.side_effect = [True, False]
    assert quickstart.run(args) == 1
    apply.assert_not_called()


def test_compose_only_does_not_connect_or_start_containers(bootstrap):
    args, start, wait, apply = bootstrap
    args.compose_only = True
    assert quickstart.run(args) == 0
    start.assert_not_called()
    wait.assert_not_called()
    apply.assert_not_called()


def test_explicit_nerode_dsn_is_exposed_by_cli():
    args = build_parser().parse_args([
        "--dsn", "trunk-explicit", "quickstart", "--nerode-dsn", "nerode-explicit",
    ])
    assert args.dsn == "trunk-explicit"
    assert args.nerode_dsn == "nerode-explicit"



def test_no_docker_requires_explicit_targets_without_files_or_connections(bootstrap):
    args, start, wait, apply = bootstrap
    args.no_docker = True
    assert quickstart.run(args) == 2
    start.assert_not_called()
    wait.assert_not_called()
    apply.assert_not_called()
    from pathlib import Path
    assert not (Path(args.dir) / quickstart.COMPOSE_FILE).exists()


def test_no_docker_applies_both_schemas_without_compose(bootstrap, monkeypatch):
    args, start, wait, apply = bootstrap
    args.no_docker = True
    monkeypatch.setenv("CALX_DSN", "postgresql://localhost:6544/test")
    monkeypatch.setenv("NERODE_DSN", "postgresql://localhost:6545/test")
    assert quickstart.run(args) == 0
    start.assert_not_called()
    apply.assert_called_once_with(
        "postgresql://localhost:6544/test", "postgresql://localhost:6545/test",
    )
    from pathlib import Path
    assert not (Path(args.dir) / quickstart.COMPOSE_FILE).exists()


def test_no_docker_rejects_compose_only(bootstrap):
    args, start, wait, apply = bootstrap
    args.no_docker = args.compose_only = True
    assert quickstart.run(args) == 2
    wait.assert_not_called()
    apply.assert_not_called()


def test_no_docker_is_exposed_by_cli():
    args = build_parser().parse_args(["quickstart", "--no-docker"])
    assert args.no_docker
