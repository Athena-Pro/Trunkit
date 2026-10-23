"""Build/install the actual wheel, then exercise consumers outside the checkout.

No package downloads or database defaults are used. The installed package is
placed first on an isolated interpreter's path; every tested module/resource
must resolve inside that wheel installation, never an editable checkout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _process(args, cwd):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=120)


@pytest.fixture(scope="module")
def installed_wheel(tmp_path_factory):
    pytest.importorskip("build")
    pytest.importorskip("hatchling")
    temp = tmp_path_factory.mktemp("installed-combinatorial-wheel")
    # An externally configured --basetemp may be inside the repository. The
    # subprocess still receives no source path; default pytest temp is outside.
    wheels, site = temp / "wheels", temp / "site"
    result = _process([sys.executable, "-m", "build", "--no-isolation", "--wheel",
                       "--outdir", str(wheels), str(ROOT)], temp)
    assert result.returncode == 0, result.stdout + result.stderr
    wheel, = wheels.glob("*.whl")
    result = _process([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                       "--no-compile", "--target", str(site), str(wheel)], temp)
    assert result.returncode == 0, result.stdout + result.stderr
    return temp, site, wheel


def _installed(installed_wheel, code, *args):
    temp, site, _wheel = installed_wheel
    bootstrap = "import sys;sys.path.insert(0,sys.argv.pop(1));" + code
    return _process([sys.executable, "-I", "-c", bootstrap, str(site), *map(str, args)], temp)


def _cli(installed_wheel, *args):
    result = _installed(installed_wheel,
                        "import runpy;sys.argv[0]='trunkit';"
                        "runpy.run_module('calx.cli',run_name='__main__')", *args)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_wheel_ships_consumer_modules_and_new_sql_schema(installed_wheel):
    _temp, site, wheel = installed_wheel
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert {
            "calx/automata_count.py", "calx/combinatorial.py", "calx/comb_cli.py",
            "calx/proposition.py", "calx/sat_adapter.py", "calx/sql/120_comb_certificates.sql",
            "nerode/sql/D0_count.sql", "trunkit_mcp/server.py",
            "calx/sql/121_calx_routine_search_path.sql", "nerode/porter.py",
            "nerode/sql/E0_porter.sql",
        } <= names
        assert not any("/.lake/" in name or name.endswith(".bak") for name in names)
        entry_point, = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        assert "trunkit = calx.cli:main" in archive.read(entry_point).decode()
    script = """
import importlib.resources as resources
import json
from pathlib import Path
from calx import automata_count, combinatorial, comb_cli, proposition, sat_adapter, db
from nerode import db as ndb
root=Path(sys.argv[1]).resolve()
for module in (automata_count,combinatorial,comb_cli,proposition,sat_adapter,db,ndb):
    assert Path(module.__file__).resolve().is_relative_to(root),module.__file__
assert '120_comb_certificates.sql' in db.UNIFIED_FILES
assert db.UNIFIED_FILES.index('120_comb_certificates.sql') < db.UNIFIED_FILES.index('121_calx_routine_search_path.sql')
assert 'D0_count.sql' in ndb.SCHEMA_FILES
assert 'E0_porter.sql' in ndb.SCHEMA_FILES
assert (resources.files('calx')/'sql'/'120_comb_certificates.sql').is_file()
assert (resources.files('nerode')/'sql'/'D0_count.sql').is_file()
assert (resources.files('calx')/'sql'/'121_calx_routine_search_path.sql').is_file()
assert (resources.files('nerode')/'sql'/'E0_porter.sql').is_file()
print(json.dumps({'installed_modules':7,'schemas_present':True}))
"""
    result = _installed(installed_wheel, script, site)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["schemas_present"]


def test_installed_cli_demo_import_count_product_and_extremum(installed_wheel):
    temp, _site, _wheel = installed_wheel
    demo = temp / "demo.json"
    _cli(installed_wheel, "comb", "demo", "--output", demo)
    _cli(installed_wheel, "comb", "check", demo)
    assert "VALID" in _cli(installed_wheel, "verify", "--bundle", demo, "--offline").stdout
    instance_path = temp / "graph.json"
    instance_path.write_text(json.dumps(json.loads(demo.read_text())["claims"][0]
                                       ["witness"]["body"]["instance"]), encoding="utf-8")
    imported = json.loads(_cli(installed_wheel, "comb", "import", instance_path).stdout)
    assert imported["instance"]["kind"] == "graph" and len(imported["instance_digest"]) == 64
    proof_path, excluded_path = temp / "colouring.drup", temp / "excluded.json"
    proof_path.write_text("-1 0\n0\n", encoding="ascii")
    _cli(installed_wheel, "comb", "exclude", instance_path, "--colours", 2,
         "--proof", proof_path, "--bundle", "--output", excluded_path)
    assert "VALID" in _cli(installed_wheel, "verify", "--bundle", excluded_path, "--offline").stdout

    dfa = {"states": [0], "alphabet": ["a", "b"], "initial": 0, "accepting": [0],
           "transitions": [[0, "a", 0], [0, "b", 0]]}
    dfa_path, count_path = temp / "dfa.json", temp / "counts.json"
    dfa_path.write_text(json.dumps(dfa), encoding="utf-8")
    _cli(installed_wheel, "dfa", "count", dfa_path, "--length", 50,
         "--annihilator", "[-2,1]", "--bundle", "--output", count_path)
    counts = json.loads(count_path.read_text())["claims"][0]["witness"]["body"]["counts"]
    assert counts[-1] == 2**50
    assert "VALID" in _cli(installed_wheel, "verify", "--bundle", count_path, "--offline").stdout
    combined = json.loads(_cli(installed_wheel, "dfa", "product", dfa_path, dfa_path).stdout)
    assert len(combined["states"]) == 1 and len(combined["transitions"]) == 2

    weighted = {"dfa": dfa, "weights": [[0, "a", -1], [0, "b", 3]]}
    weighted_path, optimum_path = temp / "weighted.json", temp / "optimum.json"
    weighted_path.write_text(json.dumps(weighted), encoding="utf-8")
    _cli(installed_wheel, "dfa", "extremal", weighted_path, "--horizon", 5,
         "--sense", "best", "--bundle", "--output", optimum_path)
    optimum = json.loads(optimum_path.read_text())["claims"][0]["witness"]["body"]
    assert optimum["value"] == 15 and optimum["history"] == ["b"] * 5
    assert "VALID" in _cli(installed_wheel, "verify", "--bundle", optimum_path, "--offline").stdout


def test_installed_mcp_discovery_and_new_proof_apis(installed_wheel):
    pytest.importorskip("mcp")
    script = """
import asyncio,json
from pathlib import Path
from trunkit_mcp import server
assert Path(server.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
names={tool.name for tool in asyncio.run(server.mcp.list_tools())}
assert {'comb_import','comb_export','comb_transform','comb_search','comb_exclude',
        'automata_count','automata_extremal','automata_product'}<=names
dfa={'states':[0],'alphabet':['a','b'],'initial':0,'accepting':[0],
     'transitions':[[0,'a',0],[0,'b',0]]}
count=server.automata_count(json.dumps(dfa),30,'[-2,1]')
assert count['verdict']=='valid' and count['witness']['counts'][-1]==2**30
weighted={'dfa':dfa,'weights':[[0,'a',-1],[0,'b',3]]}
extremum=server.automata_extremal(json.dumps(weighted),5,'worst')
assert extremum['verdict']=='valid' and extremum['witness']['value']==-5
print(json.dumps({'new_tools_available':True,'tool_count':len(names)}))
"""
    result = _installed(installed_wheel, script, installed_wheel[1])
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["new_tools_available"]
