"""The console script: `plone-importer-delta zope.conf site export --plan`.

In the style of plone.exportimport's `plone-importer`, so a deploy can run it
over ssh, `docker exec` or a one-off container job. `run` is the script once
Zope is up: the tests call it with the test site, and `main` only starts Zope
and finds the site, as `plone-importer` does.
"""

from __future__ import annotations

from collective.ploneexportimportdelta.cli import parse_args
from collective.ploneexportimportdelta.cli import run
from collective.ploneexportimportdelta.stored_base import has_base
from plone import api
from test_stored_base import ADDED
from test_stored_base import deployed  # noqa: F401
from test_stored_base import ROOT
from test_stored_base import second_layout
from test_stored_base import write_layout

import json
import pytest
import transaction


def test_plan_prints_the_plan_and_changes_nothing(deployed, tmp_path, capsys):  # noqa: F811
    portal, request, _, items = deployed
    code = run(
        portal,
        request,
        parse_args([
            "zope.conf",
            "plone",
            str(second_layout(tmp_path, items)),
            "--plan",
        ]),
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["plan"]["creates"] == ["added"]
    assert report["plan"]["updates"] == ["changed"]
    assert report["apply"]["committed"] is False
    transaction.begin()
    assert portal["changed"].title == "Before"
    assert "added" not in portal


def test_commit_applies_it(deployed, tmp_path, capsys):  # noqa: F811
    portal, request, _, items = deployed
    code = run(
        portal,
        request,
        parse_args([
            "zope.conf",
            "plone",
            str(second_layout(tmp_path, items)),
            "--commit",
        ]),
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["apply"]["committed"] is True
    transaction.begin()
    assert portal["changed"].title == "After"
    assert "added" in portal


def test_a_site_with_no_base_is_refused(functional, tmp_path, capsys):
    layout = write_layout(tmp_path / "layout", {"root": ROOT, "added": ADDED})
    code = run(
        functional["portal"],
        functional["request"],
        parse_args(["zope.conf", "plone", str(layout), "--plan"]),
    )
    assert code == 1
    assert "no base" in capsys.readouterr().err


def test_a_refused_commit_fails(deployed, tmp_path, capsys):  # noqa: F811
    portal, request, _, items = deployed
    with api.env.adopt_roles(["Manager"]):
        portal["changed"].title = "An editor's change"
        portal["changed"].reindexObject()
    transaction.commit()
    code = run(
        portal,
        request,
        parse_args([
            "zope.conf",
            "plone",
            str(second_layout(tmp_path, items)),
            "--commit",
        ]),
    )
    assert code == 1
    assert json.loads(capsys.readouterr().out)["apply"]["committed"] is False
    transaction.begin()
    assert portal["changed"].title == "An editor's change"


def test_record_base_after_a_full_import(functional, tmp_path, capsys):
    portal = functional["portal"]
    layout = write_layout(tmp_path / "layout", {"root": ROOT})
    code = run(
        portal,
        functional["request"],
        parse_args(["zope.conf", "plone", str(layout), "--record-base"]),
    )
    assert code == 0
    transaction.begin()
    assert has_base(portal)


def test_one_mode_is_required():
    with pytest.raises(SystemExit):
        parse_args(["zope.conf", "plone", "export"])
    with pytest.raises(SystemExit):
        parse_args(["zope.conf", "plone", "export", "--plan", "--commit"])


def test_ignored_fields_and_seeding_are_options():
    args = parse_args([
        "zope.conf",
        "plone",
        "export",
        "--plan",
        "--seed-from-live",
        "--ignore-field",
        "a",
        "--ignore-field",
        "b",
    ])
    assert args.seed_from_live is True
    assert args.ignore_field == ["a", "b"]
