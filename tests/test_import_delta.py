"""import_delta: a partial export, the UIDs to delete, and the catalog counter
the caller last saw.

The counter is Plone's `portal_catalog.getCounter()` (the number
plone.app.caching puts in its ETags): every catalog write adds one, so any
content change moves it and nothing else does. A deploy applies only if the
site's counter is still the one the caller has; otherwise nothing is written
and the caller exports, merges and tries again. What the delta does to each
item comes from the site: a UID it does not have is a create, one at the same
path an update, one at another path a move.
"""

from __future__ import annotations

from collective.ploneexportimportdelta.delta import import_delta
from collective.ploneexportimportdelta.delta import site_counter
from collective.ploneexportimportdelta.delta import StaleCounter
from plone import api
from plone.app.redirector.interfaces import IRedirectionStorage
from plone.exportimport.importers import get_importer
from test_deploy import ADDED
from test_deploy import CHANGED
from test_deploy import KEPT
from test_deploy import page
from test_deploy import REMOVED
from test_deploy import write_export
from zope.component import getUtility

import json
import pytest
import transaction


@pytest.fixture
def site(functional, tmp_path):
    """A site imported in full, and the counter its import left."""
    portal = functional["portal"]
    full = write_export(tmp_path / "full", [KEPT, CHANGED, REMOVED])
    with api.env.adopt_roles(["Manager"]):
        get_importer(portal).import_site(full)
    transaction.commit()
    return portal, site_counter(portal)


def deploy(portal, delta, **kw):
    with api.env.adopt_roles(["Manager"]):
        return import_delta(portal, delta, **kw)


def test_it_writes_the_delta_and_returns_the_new_counter(site, tmp_path):
    portal, counter = site
    kept_before = portal["kept"]._p_serial
    delta = write_export(tmp_path / "delta", [{**CHANGED, "title": "After"}, ADDED])
    report = deploy(portal, delta, deletes=[REMOVED["UID"]], counter=counter)
    assert report["committed"] is True, report
    assert report["plan"]["creates"] == ["added"]
    assert report["plan"]["updates"] == ["changed"]
    assert report["plan"]["deletes"] == ["removed"]
    transaction.begin()
    assert portal["changed"].title == "After"
    assert "added" in portal and "removed" not in portal
    assert portal["kept"]._p_serial == kept_before
    assert report["counter"] == site_counter(portal) != counter


def test_an_editor_s_change_since_refuses_it(site, tmp_path):
    portal, counter = site
    with api.env.adopt_roles(["Manager"]):
        portal["kept"].title = "An editor's change"
        portal["kept"].reindexObject()
    transaction.commit()
    delta = write_export(tmp_path / "delta", [ADDED])
    with pytest.raises(StaleCounter) as refused:
        deploy(portal, delta, deletes=[], counter=counter)
    assert refused.value.current == site_counter(portal) != counter
    transaction.begin()
    assert "added" not in portal


def test_a_write_that_is_not_content_does_not(site, tmp_path):
    """An image scale or a login commits, but touches no catalog entry."""
    portal, counter = site
    portal._p_jar.root()["not-content"] = 1
    transaction.commit()
    delta = write_export(tmp_path / "delta", [ADDED])
    report = deploy(portal, delta, deletes=[], counter=counter)
    assert report["committed"] is True, report


def test_no_counter_overwrites_whatever_changed(site, tmp_path):
    """For a site that is only a copy of its export."""
    portal, _ = site
    with api.env.adopt_roles(["Manager"]):
        portal["changed"].title = "An editor's change"
        portal["changed"].reindexObject()
    transaction.commit()
    delta = write_export(tmp_path / "delta", [{**CHANGED, "title": "After"}])
    report = deploy(portal, delta, deletes=[], counter=None)
    assert report["committed"] is True, report
    transaction.begin()
    assert portal["changed"].title == "After"


def test_a_dry_run_writes_nothing(site, tmp_path):
    portal, counter = site
    delta = write_export(tmp_path / "delta", [{**CHANGED, "title": "After"}, ADDED])
    report = deploy(
        portal, delta, deletes=[REMOVED["UID"]], counter=counter, dry_run=True
    )
    assert report["committed"] is False
    assert report["plan"]["creates"] == ["added"]
    transaction.begin()
    assert portal["changed"].title == "Before"
    assert "added" not in portal and "removed" in portal
    assert site_counter(portal) == counter


def test_a_uid_at_another_path_is_moved(site, tmp_path):
    portal, counter = site
    obj = portal["changed"]
    moved = page("renamed", "After", CHANGED["UID"])
    delta = write_export(tmp_path / "delta", [moved])
    report = deploy(portal, delta, deletes=[], counter=counter)
    assert report["committed"] is True, report
    assert report["plan"]["moves"] == [
        {"src": "changed", "dst": "renamed", "uid": CHANGED["UID"]}
    ]
    transaction.begin()
    assert "changed" not in portal
    assert portal["renamed"].UID() == obj.UID()
    assert portal["renamed"].title == "After"
    portal_path = "/".join(portal.getPhysicalPath())
    assert getUtility(IRedirectionStorage).get(f"{portal_path}/changed") == (
        f"{portal_path}/renamed"
    )


def test_deleting_a_uid_the_site_lacks_fails(site, tmp_path):
    portal, counter = site
    delta = write_export(tmp_path / "delta", [])
    with pytest.raises(LookupError, match="f" * 32):
        deploy(portal, delta, deletes=["f" * 32], counter=counter)


def test_a_principals_file_without_groups_or_members_adds_none(site, tmp_path):
    """Read as plone.exportimport's own importer reads it: a site that keeps
    no accounts in its export ships `{}`, and that is no principals."""
    portal, counter = site
    delta = write_export(tmp_path / "delta", [ADDED])
    (delta / "principals.json").write_text("{}")
    report = deploy(portal, delta, deletes=[], counter=counter)
    assert report["committed"] is True, report
    assert report["apply"]["principals"]["users_added"] == 0


def test_a_relation_already_on_the_site_is_left_alone(site, tmp_path):
    """relations.json is re-sent whole with every delta. Restoring and
    reindexing what is already there moved the counter on a deploy that
    changed nothing; only a missing relation is restored."""
    portal, counter = site
    relations = [
        {
            "from_uuid": KEPT["UID"],
            "to_uuid": CHANGED["UID"],
            "from_attribute": "relatedItems",
        }
    ]
    first = write_export(tmp_path / "first", [])
    (first / "relations.json").write_text(json.dumps(relations))
    report = deploy(portal, first, deletes=[], counter=counter)
    assert report["apply"]["relations"]["restored"] == 1, report
    after_first = report["counter"]
    again = write_export(tmp_path / "again", [])
    (again / "relations.json").write_text(json.dumps(relations))
    report = deploy(portal, again, deletes=[], counter=after_first)
    assert report["apply"]["relations"]["restored"] == 0, report
    assert report["counter"] == after_first == site_counter(portal)


def test_a_state_is_transitioned_without_importing_the_item(site, tmp_path):
    """A uid in `states` is transitioned to its data.json's review_state and
    not imported: an editor's change to its content is left alone."""
    portal, counter = site
    with api.env.adopt_roles(["Manager"]):
        portal["kept"].title = "Edited on the site"
    transaction.commit()
    delta = write_export(
        tmp_path / "delta", [{**KEPT, "review_state": "published"}]
    )
    report = deploy(portal, delta, deletes=[], states=[KEPT["UID"]], counter=None)
    assert report["committed"] is True, report
    assert report["plan"]["states"] == ["kept"]
    assert report["plan"]["updates"] == []
    transaction.begin()
    assert api.content.get_state(obj=portal["kept"]) == "published"
    assert portal["kept"].title == "Edited on the site"


def test_a_state_for_a_uid_the_site_lacks_fails(site, tmp_path):
    portal, counter = site
    delta = write_export(
        tmp_path / "delta", [{**ADDED, "review_state": "published"}]
    )
    with pytest.raises(LookupError):
        deploy(portal, delta, deletes=[], states=[ADDED["UID"]], counter=counter)
