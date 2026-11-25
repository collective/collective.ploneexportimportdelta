"""A deploy: the export the site was last deployed from, the new one, and the
site's record of which export that was.

The site keeps only that record (the export's id and when it was deployed),
written in the same transaction as the content. The caller fetches the export
the record names and passes it with the new one; only what changed between
them is written, and nothing is written over an editor's change.
"""

from __future__ import annotations

from collective.ploneexportimportdelta.deploy import deploy
from collective.ploneexportimportdelta.deploy import DeployRefused
from collective.ploneexportimportdelta.deployed import deployed
from collective.ploneexportimportdelta.deployed import record
from pathlib import Path
from plone import api
from plone.exportimport.importers import get_importer

import json
import pytest
import transaction


def page(id_: str, title: str, uid: str, **over) -> dict:
    return {
        "@id": f"/{id_}",
        "@type": "Document",
        "UID": uid,
        "id": id_,
        "title": title,
        **over,
    }


KEPT = page("kept", "Kept", "a" * 32)
CHANGED = page("changed", "Before", "b" * 32)
REMOVED = page("removed", "Gone", "c" * 32)
ADDED = page("added", "Added", "d" * 32)


def write_export(where: Path, items: list[dict]) -> Path:
    content = where / "content"
    content.mkdir(parents=True)
    for data in items:
        (content / data["UID"]).mkdir()
        (content / data["UID"] / "data.json").write_text(json.dumps(data))
    (content / "__metadata__.json").write_text(
        json.dumps({"_data_files_": [f"{d['UID']}/data.json" for d in items]})
    )
    return where


@pytest.fixture
def site(functional, tmp_path):
    """A site imported in full from the first export, which it records."""
    portal = functional["portal"]
    first = write_export(tmp_path / "first", [KEPT, CHANGED, REMOVED])
    with api.env.adopt_roles(["Manager"]):
        get_importer(portal).import_site(first)
        record(portal, "first")
    transaction.commit()
    return portal, first


def second(tmp_path: Path) -> Path:
    """One page changed, one removed, one added."""
    return write_export(
        tmp_path / "second", [KEPT, {**CHANGED, "title": "After"}, ADDED]
    )


def run(portal, previous, new, *, previous_id="first", new_id="second", **kw):
    with api.env.adopt_roles(["Manager"]):
        return deploy(
            portal, previous, new, previous_id=previous_id, new_id=new_id, **kw
        )


def test_it_writes_only_what_changed_and_records_the_new_export(site, tmp_path):
    portal, first = site
    kept_before = portal["kept"]._p_serial
    report = run(portal, first, second(tmp_path), commit=True)
    assert report["apply"]["committed"] is True, report
    assert report["plan"]["creates"] == ["added"]
    assert report["plan"]["updates"] == ["changed"]
    assert report["plan"]["deletes"] == ["removed"]
    transaction.begin()
    assert portal["changed"].title == "After"
    assert "added" in portal and "removed" not in portal
    assert portal["kept"]._p_serial == kept_before
    assert deployed(portal)["export"] == "second"


def test_a_dry_run_writes_nothing_and_keeps_the_record(site, tmp_path):
    portal, first = site
    report = run(portal, first, second(tmp_path), commit=False)
    assert report["apply"]["committed"] is False
    transaction.begin()
    assert portal["changed"].title == "Before"
    assert "added" not in portal
    assert deployed(portal)["export"] == "first"


def test_a_site_with_no_record_is_refused(functional, tmp_path):
    first = write_export(tmp_path / "first", [KEPT])
    with pytest.raises(DeployRefused, match="no export recorded"):
        run(functional["portal"], first, second(tmp_path), commit=True)


def test_the_previous_export_must_be_the_one_recorded(site, tmp_path):
    portal, first = site
    with pytest.raises(DeployRefused, match="'first'"):
        run(portal, first, second(tmp_path), previous_id="older", commit=True)


def test_an_editor_s_change_to_a_page_the_export_changes_is_refused(site, tmp_path):
    portal, first = site
    with api.env.adopt_roles(["Manager"]):
        portal["changed"].title = "An editor's change"
        portal["changed"].reindexObject()
    transaction.commit()
    report = run(portal, first, second(tmp_path), commit=True)
    assert report["apply"]["committed"] is False
    assert report["conflicts"] == {"changed": "changed on the site"}
    transaction.begin()
    assert portal["changed"].title == "An editor's change"
    assert "added" not in portal
    assert deployed(portal)["export"] == "first"


def test_an_editor_s_change_to_a_page_the_export_leaves_is_kept(site, tmp_path):
    portal, first = site
    with api.env.adopt_roles(["Manager"]):
        portal["kept"].title = "An editor's change"
        portal["kept"].reindexObject()
    transaction.commit()
    report = run(portal, first, second(tmp_path), commit=True)
    assert report["apply"]["committed"] is True, report
    transaction.begin()
    assert portal["kept"].title == "An editor's change"
    assert portal["changed"].title == "After"


def test_a_page_named_to_overwrite_is_taken_over_an_editor_s_change(site, tmp_path):
    portal, first = site
    with api.env.adopt_roles(["Manager"]):
        portal["changed"].title = "An editor's change"
        portal["changed"].reindexObject()
    transaction.commit()
    report = run(portal, first, second(tmp_path), commit=True, overwrite=["changed"])
    assert report["apply"]["committed"] is True, report
    transaction.begin()
    assert portal["changed"].title == "After"


def test_a_deploy_s_own_writes_are_not_an_editor_s_on_the_next(site, tmp_path):
    portal, first = site
    run(portal, first, second(tmp_path), commit=True)
    third = write_export(
        tmp_path / "third", [KEPT, {**CHANGED, "title": "After again"}, ADDED]
    )
    report = run(
        portal,
        second(tmp_path / "again"),
        third,
        previous_id="second",
        new_id="third",
        commit=True,
    )
    assert report["apply"]["committed"] is True, report
    transaction.begin()
    assert portal["changed"].title == "After again"


def test_nothing_on_the_site_is_serialized(site, tmp_path, monkeypatch):
    from plone.exportimport.exporters.content import ContentExporter

    def refuse(*args, **kwargs):
        raise AssertionError("a deploy serialized the site")

    monkeypatch.setattr(ContentExporter, "serialize", refuse)
    portal, first = site
    assert run(portal, first, second(tmp_path), commit=True)["apply"]["committed"]


def test_reset_makes_the_site_match_the_export(site, tmp_path):
    """For a site that is only a copy of its export: what the site made is
    deleted, what it lost is made again, and what it changed is taken back."""
    portal, first = site
    with api.env.adopt_roles(["Manager"]):
        api.content.create(container=portal, type="Document", id="made", title="Made")
        api.content.create(container=portal["made"], type="Document", id="inner")
        api.content.delete(obj=portal["kept"])
        portal["changed"].title = "An editor's change"
        portal["changed"].reindexObject()
    transaction.commit()
    report = run(portal, first, second(tmp_path), commit=True, reset=True)
    assert report["apply"]["committed"] is True, report
    assert report["reset"]["site_only"] == ["made"]
    assert report["reset"]["missing"] == ["kept"]
    transaction.begin()
    assert "made" not in portal
    assert portal["kept"].title == "Kept"
    assert portal["changed"].title == "After"


def test_reset_takes_back_only_what_changed_after_the_deploy(site, tmp_path):
    """The catalog's date index keeps minutes: a page the deploy itself wrote
    in the same minute is not an editor's change."""
    portal, first = site
    report = run(portal, first, first, new_id="first-again", commit=False, reset=True)
    assert report["reset"]["changed_on_site"] == []


def test_overwriting_a_page_the_export_lacks_is_refused(site, tmp_path):
    portal, first = site
    with pytest.raises(DeployRefused, match="'removed'"):
        run(portal, first, second(tmp_path), commit=True, overwrite=["/removed"])


def test_a_page_that_moves_and_changes_gets_its_new_content(site, tmp_path):
    portal, first = site
    moved = {**CHANGED, "@id": "/kept/changed", "title": "After"}
    new = write_export(tmp_path / "moved", [KEPT, moved, REMOVED])
    report = run(portal, first, new, commit=True)
    assert report["apply"]["committed"] is True, report
    transaction.begin()
    assert "changed" not in portal
    assert portal["kept"]["changed"].title == "After"
    assert ["/changed", "/kept/changed"] in report["plan"]["redirects"]


def test_a_page_that_moves_into_a_new_folder_and_changes_is_refused(site, tmp_path):
    """The update has to land on the moved page, and the page can only move
    once its new folder exists: imported first at its new path it would be a
    second object with the same UID. Refused, nothing written."""
    portal, first = site
    folder = page("new-folder", "New", "f" * 32)
    moved = {**CHANGED, "@id": "/new-folder/changed", "title": "After"}
    new = write_export(tmp_path / "moved", [KEPT, folder, moved, REMOVED])
    with pytest.raises(LookupError, match="which this delta creates"):
        run(portal, first, new, commit=True)
    transaction.abort()
    assert portal["changed"].title == "Before"
    assert "new-folder" not in portal


def test_the_whole_ordering_applies_when_nothing_else_changes(site, tmp_path):
    """For order that is wrong on pages that are not changing: they are in no
    change-set, so their positions ride along with none."""
    portal, first = site
    assert portal.getObjectPosition("kept") < portal.getObjectPosition("changed")
    new = write_export(tmp_path / "reordered", [KEPT, CHANGED, REMOVED])
    meta = new / "content" / "__metadata__.json"
    meta.write_text(
        json.dumps({
            **json.loads(meta.read_text()),
            "ordering": {CHANGED["UID"]: 0, KEPT["UID"]: 1, REMOVED["UID"]: 2},
        })
    )
    report = run(portal, first, new, commit=True, whole_metadata=True)
    assert report["apply"]["committed"] is True, report
    transaction.begin()
    assert portal.getObjectPosition("changed") < portal.getObjectPosition("kept")


def test_an_export_s_relations_are_applied(site, tmp_path):
    """plone.exportimport keeps relations in relations.json, outside each item,
    so a deploy that only imports items never sets them."""
    portal, first = site
    new = second(tmp_path)
    (new / "relations.json").write_text(
        json.dumps([
            {
                "from_uuid": KEPT["UID"],
                "to_uuid": CHANGED["UID"],
                "from_attribute": "relatedItems",
            }
        ])
    )
    report = run(portal, first, new, commit=True)
    assert report["apply"]["relations"]["restored"] == 1
    transaction.begin()
    assert [rel.to_object for rel in portal["kept"].relatedItems] == [portal["changed"]]
