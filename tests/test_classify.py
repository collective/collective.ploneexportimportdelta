"""The plan: what the new export changed against the previous one.

Two exports, no site: each test pins one row of the table in the README.
Whether an editor changed an item on the site since is `conflicts`
(test_conflicts.py).
"""

from __future__ import annotations

from collective.ploneexportimportdelta import changeset as _imod
from collective.ploneexportimportdelta import classify as _mod
from collective.ploneexportimportdelta.classify import cleaned_for_compare
from collective.ploneexportimportdelta.classify import CompareRules

import copy
import json


Action = _mod.Action
Plan = _mod.Plan
classify = _mod.classify

# A minimal local-format data.json. Volatile fields (modified/version/...) are
# present so tests can prove they're ignored for "did it change".
BASE_DOC = {
    "@id": "/services/x",
    "@type": "Document",
    "UID": "uid-x-0000000000000000000000000000",
    "id": "x",
    "title": "X",
    "description": "",
    "blocks": {"b1": {"@type": "slate", "value": [{"text": "hello"}]}},
    "blocks_layout": {"items": ["b1"]},
    "exclude_from_nav": False,
    "review_state": "published",
    "modified": "2026-06-01T00:00:00",
    "version": 1,
    "workflow_history": {"simple_publication_workflow": []},
}


def doc(rel: str, uid: str, **over):
    d = copy.deepcopy(BASE_DOC)
    d["@id"] = f"/{rel}"
    d["id"] = rel.rsplit("/", 1)[-1]
    d["UID"] = uid
    d.update(over)
    return d


def tree(*items):
    """items: (rel, data) -> {rel: data}"""
    return dict(items)


UID_X = "uid-x-0000000000000000000000000000"
UID_Y = "uid-y-0000000000000000000000000000"


def plan_of(previous, new, rules=None):
    return classify(previous, new) if rules is None else classify(previous, new, rules)


def test_an_unchanged_item_is_left_alone():
    d = doc("services/x", UID_X)
    plan = plan_of(tree(("services/x", d)), tree(("services/x", d)))
    assert plan.actions_for("services/x") == Action.NOOP
    assert plan.noops == ["services/x"]


def test_a_volatile_only_change_is_left_alone():
    previous = doc("services/x", UID_X, modified="2026-06-01T00:00:00", version=1)
    new = doc(
        "services/x",
        UID_X,
        modified="2026-06-09T12:00:00",
        version=7,
        workflow_history={"simple_publication_workflow": [{"action": "x"}]},
    )
    plan = plan_of(tree(("services/x", previous)), tree(("services/x", new)))
    assert plan.actions_for("services/x") == Action.NOOP


def test_a_changed_item_is_an_update():
    plan = plan_of(
        tree(("services/x", doc("services/x", UID_X))),
        tree(("services/x", doc("services/x", UID_X, title="X v2"))),
    )
    assert plan.actions_for("services/x") == Action.UPDATE
    assert plan.updates == ["services/x"]


def test_a_new_item_is_a_create():
    plan = plan_of(tree(), tree(("services/x", doc("services/x", UID_X))))
    assert plan.creates == ["services/x"]


def test_a_gone_item_is_a_delete():
    plan = plan_of(tree(("services/x", doc("services/x", UID_X))), tree())
    assert plan.deletes == ["services/x"]


def test_the_same_uid_at_a_new_path_is_a_move_with_a_redirect():
    plan = plan_of(
        tree(("build", doc("build", UID_X))),
        tree(("services/build", doc("services/build", UID_X))),
    )
    assert plan.moves == [_mod.Move(src="build", dst="services/build", uid=UID_X)]
    assert plan.updates == [] and plan.creates == [] and plan.deletes == []
    assert ("/build", "/services/build") in plan.redirects


def test_a_move_that_also_changes_is_an_update_of_the_new_path():
    plan = plan_of(
        tree(("build", doc("build", UID_X))),
        tree(("services/build", doc("services/build", UID_X, title="Build v2"))),
    )
    assert [m.dst for m in plan.moves] == ["services/build"]
    assert plan.updates == ["services/build"]


def test_a_change_of_state_alone_is_a_state_change_not_an_update():
    # Re-importing an item reindexes its whole text, a PDF's included: a
    # site with hundreds of files ran out of time doing that for a state.
    previous = doc("files/x", UID_X)
    del previous["review_state"]
    plan = plan_of(
        tree(("files/x", previous)),
        tree(("files/x", doc("files/x", UID_X, review_state="published"))),
    )
    assert plan.actions_for("files/x") == Action.STATE
    assert plan.states == ["files/x"]
    assert plan.updates == []
    assert plan.to_changeset()["states"] == ["files/x"]


def test_a_change_of_state_and_content_is_an_update():
    plan = plan_of(
        tree(("files/x", doc("files/x", UID_X, review_state="private"))),
        tree((
            "files/x",
            doc("files/x", UID_X, review_state="published", title="X v2"),
        )),
    )
    assert plan.actions_for("files/x") == Action.UPDATE
    assert plan.states == []


def test_fields_the_site_cannot_hold_are_left_out():
    rules = CompareRules(ignored_fields=frozenset({"legacy"}))
    plan = plan_of(
        tree(("services/x", doc("services/x", UID_X, legacy="a"))),
        tree(("services/x", doc("services/x", UID_X, legacy="b"))),
        rules,
    )
    assert plan.actions_for("services/x") == Action.NOOP


def test_to_changeset_shape():
    plan = plan_of(
        tree(("build", doc("build", UID_X))),
        tree(("services/build", doc("services/build", UID_X))),
    )
    cs = plan.to_changeset()
    assert set(cs) == {
        "creates",
        "updates",
        "states",
        "deletes",
        "moves",
        "redirects",
        "warnings",
        "base_tid",
    }
    assert cs["moves"] == [{"src": "build", "dst": "services/build", "uid": UID_X}]
    assert ["/build", "/services/build"] in cs["redirects"]


def test_a_uid_keyed_export_classifies_and_builds_its_subset(tmp_path):
    """plone.exportimport keys an export by UID (content/<uid>/data.json, the
    hierarchy only in @id): the loader keys the plan by path and hands
    build_subset_dir the dirname index."""

    def write(tree_dir, uid, at_id, title):
        d = tree_dir / uid
        d.mkdir(parents=True)
        (d / "data.json").write_text(
            json.dumps({
                "@id": at_id,
                "@type": "Document",
                "UID": uid,
                "id": at_id.rsplit("/", 1)[-1],
                "title": title,
            })
        )

    previous_dir = tmp_path / "previous"
    new_dir = tmp_path / "new"
    for uid, at_id, titles in [
        ("aaaa0000000000000000000000000001", "/about", ("About", "About")),
        ("bbbb0000000000000000000000000002", "/about/team", ("Team", "Team, renamed")),
    ]:
        write(previous_dir, uid, at_id, titles[0])
        write(new_dir, uid, at_id, titles[1])
    write(new_dir, "cccc0000000000000000000000000003", "/about/new-page", "New")

    previous, _ = _mod.load_exportimport_tree(previous_dir)
    new, new_dirs = _mod.load_exportimport_tree(new_dir)
    plan = classify(previous, new)
    assert plan.creates == ["about/new-page"]
    assert plan.updates == ["about/team"]

    cs = _imod.ChangeSet.from_json(plan.to_changeset())
    meta = _imod.build_subset_dir(
        cs, new_dir, tmp_path / "subset", dir_for=new_dirs.__getitem__
    )
    listed = meta["_data_files_"]
    assert "cccc0000000000000000000000000003/data.json" in listed
    assert listed.index("bbbb0000000000000000000000000002/data.json") <= 1


def test_an_empty_value_and_an_absent_one_are_the_same_content():
    absent = {"@id": "/x", "title": "X"}
    empty = {
        "@id": "/x",
        "title": "X",
        "changeNote": "",
        "rights": "",
        "contributors": [],
        "creators": [],
        "subjects": [],
    }
    assert _mod.content_key(absent) == _mod.content_key(empty)


def test_clearing_a_real_value_still_registers():
    """The point of the compare: emptying a field an author had filled is an
    edit, and must not be swallowed with the drift."""
    filled = {"@id": "/x", "title": "X", "description": "About X"}
    cleared = {"@id": "/x", "title": "X", "description": ""}
    assert _mod.content_key(filled) != _mod.content_key(cleared)


def test_a_page_with_no_blocks_matches_one_with_no_blocks_field():
    """A folder the export gives no blocks at all comes back from the live
    serializer as blocks {} with a blocks_layout of no items."""
    absent = {"@id": "/templates", "title": "Templates"}
    empty = {
        "@id": "/templates",
        "title": "Templates",
        "blocks": {},
        "blocks_layout": {"items": []},
    }
    assert _mod.content_key(absent) == _mod.content_key(empty)


def test_a_page_that_HAS_blocks_is_untouched_by_that():
    with_blocks = {
        "@id": "/x",
        "blocks": {"b1": {"@type": "slate"}},
        "blocks_layout": {"items": ["b1"]},
    }
    assert "b1" in _mod.content_key(with_blocks)
    assert _mod.content_key(with_blocks) != _mod.content_key({"@id": "/x"})


def test_importer_bookkeeping_is_not_a_live_edit():
    """A freshly imported object diverges from the layout tree in the fields the
    importer itself stamps — creators from the importing user, changeNote and
    friends at their defaults. Comparing those made every apply warn that an
    editor had changed a page nobody had touched, which spends the one clobber
    override on noise. Provenance is not content, and the conversion does not
    carry it."""
    layout = {"title": "Site footer", "blocks": {"a": 1}}
    live = {
        "title": "Site footer",
        "blocks": {"a": 1},
        "creators": ["admin"],
        "contributors": ["admin"],
        "changeNote": "",
        "rights": "",
        "subjects": [],
    }
    assert cleaned_for_compare(live) == cleaned_for_compare(layout)


def test_a_real_content_change_still_registers():
    """The guard above must not swallow an actual edit."""
    layout = {"title": "Site footer", "blocks": {"a": 1}}
    live = {"title": "Site footer EDITED", "blocks": {"a": 1}, "creators": ["admin"]}
    assert cleaned_for_compare(live) != cleaned_for_compare(layout)


def test_items_in_nested_folders_are_part_of_the_tree(tmp_path):
    """A layout may name its folders by path, so an item's folder can sit
    inside another item's (`videos/` and `videos/intro/`). Reading one level
    deep left every nested item out of the delta: a change to one never
    deployed."""
    import json as _json

    for dirname, at_id in (("videos", "/videos"), ("videos/intro", "/videos/intro")):
        (tmp_path / dirname).mkdir(parents=True)
        (tmp_path / dirname / "data.json").write_text(
            _json.dumps({"@id": at_id, "@type": "Document", "UID": dirname})
        )
    tree, dirs = _mod.load_exportimport_tree(tmp_path)
    assert sorted(tree) == ["videos", "videos/intro"]
    assert dirs == {"videos": "videos", "videos/intro": "videos/intro"}


def test_where_a_blob_was_exported_is_not_content():
    """`blob_path` is where an export put a file's bytes: the exporter writes
    `<uid>/file/x.mp4`, a layout with path-named folders `videos/x/file/x.mp4`.
    Same bytes, so the same content — but a real change to the file (its
    size, its name, its type) still registers."""
    layout = {
        "@id": "/v",
        "file": {"blob_path": "videos/v/file/v.mp4", "filename": "v.mp4", "size": 10},
    }
    live = {
        "@id": "/v",
        "file": {"blob_path": "0123abcd/file/v.mp4", "filename": "v.mp4", "size": 10},
    }
    assert _mod.content_key(layout) == _mod.content_key(live)
    resized = {"@id": "/v", "file": {**live["file"], "size": 11}}
    assert _mod.content_key(resized) != _mod.content_key(live)


def test_parent_follows_from_the_path_and_is_not_content():
    """The live exporter leaves `parent` out; a layout may carry it. It is
    derived from `@id`, so a move still registers through `@id`."""
    with_parent = {"@id": "/videos/v", "title": "V", "parent": {"@id": "/videos"}}
    without = {"@id": "/videos/v", "title": "V"}
    assert _mod.content_key(with_parent) == _mod.content_key(without)


def test_an_item_on_its_types_default_view_is_unset():
    """Plone reports an item's default view as `layout: <default view>`, and a
    converted tree leaves the key out. That holds for any type the site has,
    its own included, by the default view the site gives it. Another view is
    an editor's choice and still counts."""
    rules = CompareRules(
        default_views={"Document": "document_view", "SiteOwnType": "own_view"}
    )
    for portal_type, default in (
        ("Document", "document_view"),
        ("SiteOwnType", "own_view"),
    ):
        live = doc(
            "templates/footer", UID_X, **{"@type": portal_type, "layout": default}
        )
        disk = doc("templates/footer", UID_X, **{"@type": portal_type})
        assert cleaned_for_compare(live, rules) == cleaned_for_compare(disk, rules)
        chosen = doc(
            "templates/footer",
            UID_X,
            **{"@type": portal_type, "layout": "summary_view"},
        )
        assert cleaned_for_compare(chosen, rules) != cleaned_for_compare(disk, rules)


def test_the_site_root_is_the_export_root_whatever_the_site_is_called(tmp_path):
    """The root is the item the export keeps in `plone_site_root`, the export
    format's own name for it. Not a site called `Plone` (a site's id is its
    own: `/agency`), and not a type name: a page whose id is `Plone` is a page."""
    import json as _json

    items = (
        (
            "plone_site_root",
            {"@id": "/agency", "@type": "SiteRootOfAnyType", "UID": "root"},
        ),
        ("aaa", {"@id": "/Plone", "@type": "Document", "UID": "aaa"}),
    )
    for dirname, data in items:
        (tmp_path / dirname).mkdir(parents=True)
        (tmp_path / dirname / "data.json").write_text(_json.dumps(data))
    tree, dirs = _mod.load_exportimport_tree(tmp_path)
    assert tree["plone_site_root"]["UID"] == "root"
    assert tree["Plone"]["UID"] == "aaa"
    assert dirs == {"plone_site_root": "plone_site_root", "Plone": "aaa"}


def test_a_field_at_its_types_default_is_unset():
    """The live serializer writes a field its type gives a default
    (`exclude_from_nav: false`) where a layout leaves it out. The default and
    unset are the same state, as `null`, `""` and `[]` are; any other value is
    a choice and still counts. Matched by type too: `0` is not `false`."""
    rules = CompareRules(
        field_defaults={"Document": {"exclude_from_nav": False, "count": 0}}
    )
    disk = {
        k: v
        for k, v in doc("components/form", UID_X).items()
        if k != "exclude_from_nav"
    }
    at_default = doc("components/form", UID_X, exclude_from_nav=False)
    chosen = doc("components/form", UID_X, exclude_from_nav=True)
    assert cleaned_for_compare(at_default, rules) == cleaned_for_compare(disk, rules)
    assert cleaned_for_compare(chosen, rules) != cleaned_for_compare(disk, rules)
    not_the_default = doc("components/form", UID_X, count=False)
    assert "count" in cleaned_for_compare(not_the_default, rules)
    # Another type's defaults are not this one's.
    other = doc(
        "components/form", UID_X, **{"@type": "News Item", "exclude_from_nav": False}
    )
    assert "exclude_from_nav" in cleaned_for_compare(other, rules)
