"""The console script: `plone-importer-delta zope.conf site ...`.

In the style of plone.exportimport's `plone-importer`, so a deploy can run it
over ssh, `docker exec` or a one-off container job. `run` is the script once
Zope is up: the tests call it with the test site, and `main` only starts Zope
and finds the site, as `plone-importer` does.
"""

from __future__ import annotations

from collective.ploneexportimportdelta.cli import parse_args
from collective.ploneexportimportdelta.cli import run
from collective.ploneexportimportdelta.deployed import deployed
from plone import api
from test_deploy import CHANGED
from test_deploy import KEPT
from test_deploy import second
from test_deploy import site  # noqa: F401
from test_deploy import write_export

import json
import pytest
import transaction


def deploy_args(first, new, *mode):
    return parse_args([
        "zope.conf",
        "plone",
        "--previous",
        str(first),
        "--previous-id",
        "first",
        "--new",
        str(new),
        "--new-id",
        "second",
        *mode,
    ])


def test_show_prints_the_export_the_site_was_deployed_from(site, capsys):  # noqa: F811
    portal, _ = site
    assert run(portal, parse_args(["zope.conf", "plone", "--show"])) == 0
    assert json.loads(capsys.readouterr().out)["export"] == "first"


def test_plan_prints_the_plan_and_changes_nothing(site, tmp_path, capsys):  # noqa: F811
    portal, first = site
    assert run(portal, deploy_args(first, second(tmp_path), "--plan")) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["plan"]["updates"] == ["changed"]
    assert report["apply"]["committed"] is False
    transaction.begin()
    assert portal["changed"].title == "Before"


def test_commit_applies_it(site, tmp_path, capsys):  # noqa: F811
    portal, first = site
    assert run(portal, deploy_args(first, second(tmp_path), "--commit")) == 0
    assert json.loads(capsys.readouterr().out)["apply"]["committed"] is True
    transaction.begin()
    assert portal["changed"].title == "After"
    assert deployed(portal)["export"] == "second"


def test_a_conflict_fails_and_writes_nothing(site, tmp_path, capsys):  # noqa: F811
    portal, first = site
    with api.env.adopt_roles(["Manager"]):
        portal["changed"].title = "An editor's change"
        portal["changed"].reindexObject()
    transaction.commit()
    assert run(portal, deploy_args(first, second(tmp_path), "--commit")) == 1
    assert json.loads(capsys.readouterr().out)["conflicts"] == {
        "changed": "changed on the site"
    }
    transaction.begin()
    assert portal["changed"].title == "An editor's change"


def test_overwrite_takes_a_page_over_an_editor_s_change(site, tmp_path, capsys):  # noqa: F811
    portal, first = site
    with api.env.adopt_roles(["Manager"]):
        portal["changed"].title = "An editor's change"
        portal["changed"].reindexObject()
    transaction.commit()
    args = deploy_args(first, second(tmp_path), "--commit", "--overwrite", "/changed")
    assert run(portal, args) == 0
    transaction.begin()
    assert portal["changed"].title == "After"


def test_the_wrong_previous_export_is_refused(site, tmp_path, capsys):  # noqa: F811
    portal, _ = site
    older = write_export(tmp_path / "older", [KEPT, CHANGED])
    args = parse_args([
        "zope.conf",
        "plone",
        "--previous",
        str(older),
        "--previous-id",
        "older",
        "--new",
        str(second(tmp_path)),
        "--new-id",
        "second",
        "--commit",
    ])
    assert run(portal, args) == 1
    assert "'first'" in capsys.readouterr().err


def test_record_after_a_full_import(functional, capsys):
    portal = functional["portal"]
    assert run(portal, parse_args(["zope.conf", "plone", "--record", "imported"])) == 0
    transaction.begin()
    assert deployed(portal)["export"] == "imported"


def test_one_mode_is_required():
    with pytest.raises(SystemExit):
        parse_args(["zope.conf", "plone"])
    with pytest.raises(SystemExit):
        parse_args(["zope.conf", "plone", "--show", "--record", "x"])


def test_a_deploy_needs_both_exports_and_their_ids():
    with pytest.raises(SystemExit):
        parse_args(["zope.conf", "plone", "--plan", "--new", "n", "--new-id", "2"])
