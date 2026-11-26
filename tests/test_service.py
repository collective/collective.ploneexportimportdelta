"""`@import-delta`: import_delta over REST, beside plone.exportimport's
`@import`, with the same permission.

GET answers the catalog counter; read it before exporting, so that whatever
changes during the export moves it. POST takes the partial export as a zip
(`file`, as `@import` does), `deletes` (a JSON list of UIDs), and either that
`counter` or `force`.
"""

from __future__ import annotations

from collective.ploneexportimportdelta.delta import site_counter
from plone import api
from plone.app.testing import SITE_OWNER_NAME
from plone.app.testing import SITE_OWNER_PASSWORD
from plone.app.testing import TEST_USER_NAME
from plone.app.testing import TEST_USER_PASSWORD
from plone.exportimport.importers import get_importer
from plone.exportimport.utils.zip_utils import export_to_zip
from test_deploy import ADDED
from test_deploy import CHANGED
from test_deploy import KEPT
from test_deploy import REMOVED
from test_deploy import write_export

import json
import pytest
import requests
import transaction


@pytest.fixture
def site(server, tmp_path):
    """A site imported in full, served over HTTP, and its counter."""
    portal = server["portal"]
    full = write_export(tmp_path / "full", [KEPT, CHANGED, REMOVED])
    with api.env.adopt_roles(["Manager"]):
        get_importer(portal).import_site(full)
    transaction.commit()
    return portal, site_counter(portal)


def call(portal, method: str, data: dict | None = None, export=None, auth=None):
    response = requests.request(
        method,
        f"{portal.absolute_url()}/@import-delta",
        headers={"Accept": "application/json"},
        auth=auth or (SITE_OWNER_NAME, SITE_OWNER_PASSWORD),
        data=data,
        files=None if export is None else {"file": export_to_zip(export)},
        timeout=60,
    )
    transaction.begin()  # see what the server committed
    return response.json(), response.status_code


def test_get_answers_the_counter(site):
    portal, counter = site
    body, status = call(portal, "GET")
    assert status == 200
    assert body == {"counter": counter}


def test_post_deploys_and_answers_the_next_counter(site, tmp_path):
    portal, counter = site
    delta = write_export(tmp_path / "delta", [{**CHANGED, "title": "After"}, ADDED])
    data = {"deletes": json.dumps([REMOVED["UID"]]), "counter": str(counter)}
    body, status = call(portal, "POST", data, delta)
    assert status == 200, body
    assert body["committed"] is True
    assert portal["changed"].title == "After"
    assert "removed" not in portal
    assert body["counter"] == site_counter(portal)


def test_a_dry_run_answers_the_plan(site, tmp_path):
    portal, counter = site
    delta = write_export(tmp_path / "delta", [ADDED])
    data = {"counter": str(counter), "dry_run": "true"}
    body, status = call(portal, "POST", data, delta)
    assert status == 200, body
    assert body["committed"] is False
    assert body["plan"]["creates"] == ["added"]
    assert "added" not in portal


def test_a_stale_counter_is_a_409_with_the_current_one(site, tmp_path):
    portal, counter = site
    with api.env.adopt_roles(["Manager"]):
        portal["kept"].reindexObject()
    transaction.commit()
    delta = write_export(tmp_path / "delta", [ADDED])
    body, status = call(portal, "POST", {"counter": str(counter)}, delta)
    assert status == 409
    assert body["counter"] == site_counter(portal) != counter
    assert "added" not in portal


def test_force_takes_the_place_of_a_counter(site, tmp_path):
    portal, _ = site
    with api.env.adopt_roles(["Manager"]):
        portal["kept"].reindexObject()
    transaction.commit()
    delta = write_export(tmp_path / "delta", [ADDED])
    body, status = call(portal, "POST", {"force": "true"}, delta)
    assert status == 200, body
    assert body["committed"] is True


@pytest.mark.parametrize(
    "check",
    [{}, {"counter": "1", "force": "true"}],
    ids=["neither", "both"],
)
def test_it_needs_a_counter_or_force(site, tmp_path, check):
    portal, _ = site
    delta = write_export(tmp_path / "delta", [ADDED])
    body, status = call(portal, "POST", check, delta)
    assert status == 400
    assert "counter" in body["error"]


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_it_needs_the_import_permission(site, tmp_path, method):
    portal, counter = site
    delta = write_export(tmp_path / "delta", [ADDED])
    _, status = call(
        portal,
        method,
        {"counter": str(counter)},
        delta if method == "POST" else None,
        auth=(TEST_USER_NAME, TEST_USER_PASSWORD),
    )
    assert status == 401
    assert "added" not in portal
