"""Conflicts: the items a deploy would write that an editor changed on the
site since the last deploy.

Read from the catalog for just those items (their modification date and
review state), never by serializing them: a change after the last deploy is
an editor's, since a deploy's own writes are older than the time it records.
"""

from __future__ import annotations

from collective.ploneexportimportdelta.classify import classify
from collective.ploneexportimportdelta.classify import conflicts
from DateTime import DateTime


SINCE = DateTime("2026-11-20T00:00:00+00:00")
BEFORE = DateTime("2026-11-19T00:00:00+00:00")
AFTER = DateTime("2026-11-21T00:00:00+00:00")


def item(rel, uid, **over):
    return {"@id": f"/{rel}", "@type": "Document", "UID": uid, "title": rel, **over}


def site(modified=BEFORE, review_state="published"):
    return {"modified": modified, "review_state": review_state}


def test_an_update_of_an_item_nobody_touched_is_no_conflict():
    previous = {"x": item("x", "u1")}
    new = {"x": item("x", "u1", title="new")}
    plan = classify(previous, new)
    assert conflicts(plan, previous, new, {"x": site()}, SINCE) == {}


def test_an_update_of_an_item_edited_since_is_a_conflict():
    previous = {"x": item("x", "u1")}
    new = {"x": item("x", "u1", title="new")}
    plan = classify(previous, new)
    assert conflicts(plan, previous, new, {"x": site(AFTER)}, SINCE) == {
        "x": "changed on the site"
    }


def test_an_update_of_an_item_gone_from_the_site_is_a_conflict():
    previous = {"x": item("x", "u1")}
    new = {"x": item("x", "u1", title="new")}
    plan = classify(previous, new)
    assert conflicts(plan, previous, new, {"x": None}, SINCE) == {
        "x": "not on the site"
    }


def test_an_edit_to_an_item_the_export_did_not_change_is_no_conflict():
    previous = {"x": item("x", "u1"), "y": item("y", "u2")}
    new = {"x": item("x", "u1"), "y": item("y", "u2", title="new")}
    plan = classify(previous, new)
    assert conflicts(plan, previous, new, {"y": site()}, SINCE) == {}


def test_deleting_an_item_edited_since_is_a_conflict():
    previous = {"x": item("x", "u1")}
    new = {}
    plan = classify(previous, new)
    assert conflicts(plan, previous, new, {"x": site(AFTER)}, SINCE) == {
        "x": "changed on the site"
    }


def test_deleting_an_item_already_gone_is_no_conflict():
    previous = {"x": item("x", "u1")}
    new = {}
    plan = classify(previous, new)
    assert conflicts(plan, previous, new, {"x": None}, SINCE) == {}


def test_moving_an_item_edited_since_is_a_conflict():
    previous = {"a": item("a", "u1")}
    new = {"b": item("b", "u1")}
    plan = classify(previous, new)
    assert conflicts(plan, previous, new, {"a": site(AFTER)}, SINCE) == {
        "a": "changed on the site"
    }


def test_creating_a_path_the_site_already_has_is_a_conflict():
    new = {"x": item("x", "u1")}
    plan = classify({}, new)
    assert conflicts(plan, {}, new, {"x": site()}, SINCE) == {
        "x": "already on the site"
    }


def test_a_state_change_over_an_editor_s_change_of_state_is_a_conflict():
    previous = {"x": item("x", "u1", review_state="private")}
    new = {"x": item("x", "u1", review_state="published")}
    plan = classify(previous, new)
    assert conflicts(
        plan, previous, new, {"x": site(review_state="pending")}, SINCE
    ) == {"x": "its state changed on the site"}


def test_a_state_change_over_an_editor_s_content_edit_is_no_conflict():
    # Only the state is applied, so the editor's text stays as it is.
    previous = {"x": item("x", "u1", review_state="private")}
    new = {"x": item("x", "u1", review_state="published")}
    plan = classify(previous, new)
    assert (
        conflicts(
            plan, previous, new, {"x": site(AFTER, review_state="private")}, SINCE
        )
        == {}
    )


def test_a_state_change_to_the_state_the_site_already_has_is_no_conflict():
    # Files given a state by the export that the site already gave them.
    previous = {"x": item("x", "u1")}
    new = {"x": item("x", "u1", review_state="published")}
    plan = classify(previous, new)
    assert (
        conflicts(plan, previous, new, {"x": site(review_state="published")}, SINCE)
        == {}
    )


def test_the_items_to_look_up_are_only_those_the_plan_writes():
    previous = {"x": item("x", "u1"), "y": item("y", "u2"), "a": item("a", "u3")}
    plan = classify(
        previous,
        {
            "x": item("x", "u1", title="new"),
            "y": item("y", "u2"),
            "b": item("b", "u3"),
            "c": item("c", "u4"),
        },
    )
    assert plan.written() == {"x", "a", "c"}


def test_an_update_over_an_editor_s_change_of_state_is_a_conflict():
    # Publishing or retracting does not move an item's modification date.
    previous = {"x": item("x", "u1", review_state="published")}
    new = {"x": item("x", "u1", review_state="published", title="new")}
    plan = classify(previous, new)
    assert conflicts(
        plan, previous, new, {"x": site(review_state="private")}, SINCE
    ) == {"x": "its state changed on the site"}
