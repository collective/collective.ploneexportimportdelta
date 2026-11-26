"""plan_delta: what `@import-delta` is sent, from the export the site was last
deployed from and the new one. No site is needed.

The delta is a plone.exportimport export of the items to create, update or
move (a moved item is sent at its new path; the site finds it by UID), and
`deletes.json`, the UIDs of the items the new export no longer has.
"""

from __future__ import annotations

from collective.ploneexportimportdelta.delta import import_delta
from collective.ploneexportimportdelta.delta import site_counter
from collective.ploneexportimportdelta.plan import plan_delta
from plone import api
from test_deploy import ADDED
from test_deploy import CHANGED
from test_deploy import KEPT
from test_deploy import page
from test_deploy import REMOVED
from test_deploy import second
from test_deploy import write_export
from test_import_delta import site  # noqa: F401

import json
import transaction


def shipped(delta) -> list[str]:
    metadata = json.loads((delta / "content" / "__metadata__.json").read_text())
    return [
        json.loads((delta / "content" / data_file).read_text())["@id"]
        for data_file in metadata["_data_files_"]
    ]


def test_it_sends_only_what_changed_and_the_uids_to_delete(tmp_path):
    previous = write_export(tmp_path / "previous", [KEPT, CHANGED, REMOVED])
    delta = tmp_path / "delta"
    report = plan_delta(previous, second(tmp_path), delta)
    assert sorted(shipped(delta)) == ["/added", "/changed"]
    assert json.loads((delta / "deletes.json").read_text()) == [REMOVED["UID"]]
    assert report["creates"] == ["added"]
    assert report["updates"] == ["changed"]
    assert report["deletes"] == ["removed"]


def test_a_moved_item_is_sent_at_its_new_path(tmp_path):
    previous = write_export(tmp_path / "previous", [KEPT, CHANGED])
    new = write_export(
        tmp_path / "new", [KEPT, page("renamed", "Before", CHANGED["UID"])]
    )
    delta = tmp_path / "delta"
    report = plan_delta(previous, new, delta)
    assert shipped(delta) == ["/renamed"]
    assert json.loads((delta / "deletes.json").read_text()) == []
    assert report["moves"] == [{"src": "changed", "dst": "renamed"}]


def test_a_state_change_is_sent(tmp_path):
    previous = write_export(
        tmp_path / "previous", [{**KEPT, "review_state": "private"}]
    )
    new = write_export(tmp_path / "new", [{**KEPT, "review_state": "published"}])
    delta = tmp_path / "delta"
    plan_delta(previous, new, delta)
    assert shipped(delta) == ["/kept"]


def test_nothing_changed_sends_nothing(tmp_path):
    previous = write_export(tmp_path / "previous", [KEPT])
    new = write_export(tmp_path / "new", [KEPT])
    delta = tmp_path / "delta"
    plan_delta(previous, new, delta)
    assert shipped(delta) == []
    assert json.loads((delta / "deletes.json").read_text()) == []


def test_the_delta_brings_the_site_to_the_new_export(site, tmp_path):  # noqa: F811
    portal, counter = site
    kept_before = portal["kept"]._p_serial
    previous = write_export(tmp_path / "previous", [KEPT, CHANGED, REMOVED])
    delta = tmp_path / "delta"
    plan_delta(previous, second(tmp_path), delta)
    with api.env.adopt_roles(["Manager"]):
        report = import_delta(
            portal,
            delta,
            deletes=json.loads((delta / "deletes.json").read_text()),
            counter=counter,
        )
    assert report["committed"] is True, report
    transaction.begin()
    assert sorted(portal.contentIds()) == ["added", "changed", "kept"]
    assert portal["changed"].title == "After"
    assert portal["added"].UID() == ADDED["UID"]
    assert portal["kept"]._p_serial == kept_before
    assert report["counter"] == site_counter(portal)


def test_any_field_that_changed_is_sent(tmp_path):
    """Both exports come from the same place, so any difference is a change:
    tags, dates and rights are content here."""
    previous = write_export(tmp_path / "previous", [KEPT])
    new = write_export(tmp_path / "new", [{**KEPT, "subjects": ["accessibility"]}])
    delta = tmp_path / "delta"
    plan_delta(previous, new, delta)
    assert shipped(delta) == ["/kept"]


def test_a_file_whose_bytes_changed_is_sent(tmp_path):
    image = {**KEPT, "image": {"blob_path": f"{KEPT['UID']}/image/a.png"}}
    previous = write_export(tmp_path / "previous", [image])
    new = write_export(tmp_path / "new", [image])
    for export, data in ((previous, b"old"), (new, b"new")):
        (export / "content" / KEPT["UID"] / "image").mkdir()
        (export / "content" / KEPT["UID"] / "image" / "a.png").write_bytes(data)
    delta = tmp_path / "delta"
    plan_delta(previous, new, delta)
    assert shipped(delta) == ["/kept"]
    assert (delta / "content" / KEPT["UID"] / "image" / "a.png").read_bytes() == b"new"


def test_an_ignored_field_is_not_a_change(tmp_path):
    previous = write_export(tmp_path / "previous", [KEPT])
    new = write_export(tmp_path / "new", [{**KEPT, "subjects": ["accessibility"]}])
    delta = tmp_path / "delta"
    plan_delta(previous, new, delta, ignored_fields=frozenset({"subjects"}))
    assert shipped(delta) == []
