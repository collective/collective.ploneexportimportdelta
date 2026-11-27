"""The plan: what a new export changed against the previous one.

classify compares two exports, the one the site was last deployed from and the
new one, and gives per item the action a deploy takes. conflicts then says
which of the items the plan writes an editor changed on the site since the last
deploy, from just those items' catalog entries. Both are pure: nothing here
reads the site or writes anything. See the README for the model.
"""

from __future__ import annotations

from .changeset import item_data_files
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from types import MappingProxyType
from typing import Any
from typing import TYPE_CHECKING
from collections.abc import Mapping

import enum
import json


if TYPE_CHECKING:
    # Only a type here: the planner (plan.py) loads this module on a plain
    # Python, without Zope.
    from DateTime import DateTime


# Where an export keeps the site root: plone.exportimport's own name for it.
SITE_ROOT = "plone_site_root"


# Fields that do NOT constitute a meaningful content change, so two data.jsons
# differing only here compare equal. Timestamps/version/history are volatile;
# the rest are derived fields the exporter emits and the importer ignores.
VOLATILE: frozenset[str] = frozenset({
    # volatile metadata. created and effective are volatile too: a converted
    # tree can store naive local datetimes while the live site serializes
    # tz-aware UTC, so they never compare equal and every page false-flagged
    # as a live editor edit (71 clobbers on a site nobody edited). Date
    # corrections don't sync through the delta as a result.
    "created",
    "effective",
    # language: the layout stores "##DEFAULT##" while the live exporter
    # resolves it (usually "en") — absent-vs-resolved never compares equal,
    # so every translated page warned. allow_discussion: None == False
    # (discussion off). Volatile for the COMPARE only — imports still apply
    # the disk values.
    "language",
    "allow_discussion",
    "modified",
    "modification_date",
    "expires",
    "version",
    "version_id",
    "workflow_history",
    "lock",
    "working_copy",
    "working_copy_of",
    "cmf_uid",
    # creators: the export records none at all and the import stamps whoever
    # ran it ("admin"), so a value can never meet an unset — chrome
    # pages warned as live editor edits on every deploy over this alone.
    # Provenance, not content; the import still applies the disk value.
    "creators",
    # derived / exportimport-internal
    "breadcrumbs",
    "navigation",
    "navroot",
    "contextnavigation",
    "next_item",
    "previous_item",
    "contentinfo",
    "siteinfo",
    "types",
    "actions",
    "@components",
    "resolved_uids",
    "items_total",
    "is_folderish",
    "type_title",
    "workflow",
    # derived from @id, which carries the path (and so any move); the live
    # exporter leaves it out while a hand-written layout may not.
    "parent",
    # per-object system flags a live export carries but hand-maintained git does
    # not: enabling versioning on an object bumps `versioning_enabled`, which must
    # NOT register as a content divergence (else every deploy of that object is
    # falsely refused as a clobber). `exportimport.*` plumbing is handled by the
    # prefix filter in content_key() below.
    "versioning_enabled",
    # Editorial bookkeeping the IMPORTER writes, not content anyone authored.
    # ContentImporter stamps creators/contributors from the importing user and
    # leaves changeNote/rights/subjects at their defaults, so a freshly applied
    # object diverges from the layout tree in exactly these fields — and the
    # NEXT apply then reads that divergence as "an editor changed this page".
    # templates/site-footer warned on every run for precisely this, which spends
    # the one clobber override on a page nobody touched. A real change to any of
    # them is not content: it is provenance, and the conversion does not carry it.
    "changeNote",
    "contributors",
    "rights",
    "subjects",
})


class Action(enum.Enum):
    NOOP = "noop"
    CREATE = "create"
    UPDATE = "update"
    # Only the review state changed: a transition, not a re-import. Importing
    # reindexes the item's whole text (a PDF's included), so a layout that
    # starts declaring its files' states re-read every file.
    STATE = "state"
    DELETE = "delete"
    MOVE = "move"


@dataclass
class Move:
    src: str
    dst: str
    uid: str


@dataclass
class Plan:
    creates: list[str] = field(default_factory=list)
    updates: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    moves: list[Move] = field(default_factory=list)
    noops: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    redirects: list[tuple[str, str]] = field(default_factory=list)
    # live ZODB tid this plan was classified against (stamped into the change-set
    # as base_tid so the apply engine can reject a stale change-set — Gate 2).
    base_tid: str | None = None
    _action: dict[str, Action] = field(default_factory=dict)

    def actions_for(self, rel: str) -> Action:
        return self._action.get(rel, Action.NOOP)

    def written(self) -> set[str]:
        """The site paths a deploy of this plan writes, as they are on the site
        now: a move's item is still at its old path."""
        moved_to = {m.dst for m in self.moves}
        return {
            *self.creates,
            *(rel for rel in self.updates if rel not in moved_to),
            *self.states,
            *self.deletes,
            *(m.src for m in self.moves),
        }

    def to_changeset(self) -> dict:
        """Serialize to the change-set the apply endpoint consumes."""
        return {
            "creates": list(self.creates),
            "updates": list(self.updates),
            "deletes": list(self.deletes),
            "moves": [{"src": m.src, "dst": m.dst, "uid": m.uid} for m in self.moves],
            "redirects": [list(r) for r in self.redirects],
            "warnings": list(self.warnings),
            "base_tid": self.base_tid,
            "states": list(self.states),
        }


@dataclass(frozen=True)
class CompareRules:
    """What a site leaves out when the delta compares two copies of an item.

    `ignored_fields`: the site's own fields it cannot round-trip. The import
    can't set what no schema declares and the live export can't emit it, so
    every touched page would warn forever. A capability gap is not an editor
    edit; the tree keeps the data.

    `default_views`: each content type's default view, as the site's type
    settings give it (delta.default_views_of). An item on it has no view of its
    own, whichever way a copy spells that.

    `field_defaults`: each content type's fields and their schema defaults
    (delta.field_defaults_of). A field at its default is unset, whichever way
    a copy spells that: the live serializer writes `exclude_from_nav: false`
    where a layout leaves it out."""

    ignored_fields: frozenset[str] = frozenset()
    default_views: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    field_defaults: Mapping[str, Mapping[str, Any]] = field(
        default_factory=lambda: MappingProxyType({})
    )


NO_RULES = CompareRules()


def cleaned_for_compare(
    data: dict[str, Any], rules: CompareRules = NO_RULES
) -> dict[str, Any]:
    """The fields that PARTICIPATE in the delta compare — the single filter
    content_key and the clobber-debug field diff share, so debug can never
    name a field the compare ignores (or miss one it does use).

    v is not None: a field serialized as null and a field absent are the same
    unset state — the live exporter emits nulls (e.g. nav_title) that the
    layout trees omit, and keeping them made every such page a false clobber.
    A REAL clearing still registers: value -> null drops the key on one side
    only. "##DEFAULT##" is exportimport's site-default sentinel = unset.

    An EMPTY value is the same unset state for the same reason: the live
    serializer writes "" / [] / {} where the layout leaves the field out.
    Chrome pages warned as live editor edits on every deploy over exactly
    this — changeNote, rights, contributors, creators, subjects. A real
    clearing still registers here too: the filled side keeps the key.

    `rules`: what this site leaves out of the compare (CompareRules)."""
    cleaned = {
        k: v
        for k, v in data.items()
        if v is not None
        and v != "##DEFAULT##"
        and v != ""
        and v != []
        and v != {}
        and k not in VOLATILE
        and k not in rules.ignored_fields
        and not k.startswith("exportimport.")
    }
    # A page with NO blocks: the live serializer says blocks {} (dropped as
    # empty just above) and blocks_layout {"items": []} — not empty itself, a
    # dict holding an empty list — while the export omits both. Same page.
    # The rule lives HERE, in the shared filter: put it in content_key alone
    # and the clobber debug names blocks_layout on a page the compare treats
    # as identical, which is exactly what this docstring promises it cannot.
    if not cleaned.get("blocks") and not (cleaned.get("blocks_layout") or {}).get(
        "items"
    ):
        cleaned.pop("blocks", None)
        cleaned.pop("blocks_layout", None)
    # An item on its type's default view is the same unset state: Plone sets
    # `layout: document_view` when it creates a page, and a converted tree
    # leaves the key out, so every page a delta CREATED warned as a live edit
    # the first time a later delta updated it (72 pages and two layout
    # templates on one conversion). A site's own types count the same: an
    # item whose type changes in place keeps no layout of its own. Another
    # view is an editor's choice and still counts.
    layout = cleaned.get("layout")
    if layout is not None and layout == rules.default_views.get(cleaned.get("@type")):
        cleaned.pop("layout")
    # A field at its type's default is the same unset state: the live
    # serializer writes `exclude_from_nav: false` on a page nobody touched,
    # where the layout leaves the field out, and a deploy refused such a page
    # as an editor's edit. Same value AND same type: `false` is not `0`.
    defaults = rules.field_defaults.get(cleaned.get("@type"), {})
    for key in [
        k
        for k, v in cleaned.items()
        if k in defaults and type(v) is type(defaults[k]) and v == defaults[k]
    ]:
        cleaned.pop(key)
    # A file's `blob_path` is where an export put its bytes: `<uid>/file/x` from
    # the exporter, `videos/x/file/x` in a layout whose folders are named by
    # path. Every such file warned as a live edit. Its name, type and size are
    # the file; where it was exported to is not.
    for key, value in list(cleaned.items()):
        if isinstance(value, dict) and "blob_path" in value:
            cleaned[key] = {k: v for k, v in value.items() if k != "blob_path"}
    return cleaned


def content_key(data: dict[str, Any], rules: CompareRules = NO_RULES) -> str:
    """Canonical JSON of the meaningful content (volatile fields removed)."""
    cleaned = cleaned_for_compare(data, rules)
    # Author-date fields (created/effective) ARE synced, but the live export and
    # hand-maintained git serialize them with different sub-minute precision / tz
    # (e.g. "2023-07-12T21:29:00+00:00" vs "2023-07-12T21:29:42"). A 42-second
    # difference is serialization noise, not a content edit — normalize to minute
    # precision so it can't register as a divergence (and falsely clobber a deploy).
    for _dk in ("effective", "created"):
        _v = cleaned.get(_dk)
        if isinstance(_v, str) and len(_v) >= 16 and _v[10:11] == "T":
            cleaned[_dk] = _v[:16]
    return json.dumps(cleaned, sort_keys=True, ensure_ascii=False)


def rel_to_path(rel: str) -> str:
    return "/" + rel


def _uid_index(tree: dict[str, dict]) -> dict[str, str]:
    return {d["UID"]: rel for rel, d in tree.items() if d.get("UID")}


def load_exportimport_tree(content_dir):
    """Load a plone.exportimport content dir into the classifier's shape:
    ({rel: data}, {rel: dirname}). The rel-path comes from each item's @id
    (an export's dirname is a UID and carries no hierarchy; a layout's may be
    a path, at any depth); pass the second mapping as ``dir_for`` to
    build_subset_dir/apply_changeset. The site root is the item the export
    keeps in 'plone_site_root', the format's own name for it: not a site
    called 'Plone' (a site's id is its own) and not a type name.
    """
    content_dir = Path(content_dir)
    tree: dict[str, dict] = {}
    dirs: dict[str, str] = {}
    for data_file in item_data_files(content_dir):
        data = json.loads(data_file.read_text())
        dirname = data_file.parent.relative_to(content_dir).as_posix()
        rel = SITE_ROOT if dirname == SITE_ROOT else (data.get("@id") or "").lstrip("/")
        if not rel:
            continue
        tree[rel] = data
        dirs[rel] = dirname
    return tree, dirs


def layout_redirects(
    disk_raw: dict[str, str], base_raw: dict[str, str], site_path: str
) -> list[tuple[str, str]]:
    """The layout's redirects.json entries the base did not already carry, as
    site-relative (old, new) pairs for the change-set — apply_redirects adds
    the physical prefix back, and keeps any redirect live already has.

    `site_path` is the site's physical path; the conversion writes every entry
    under it (normalise_redirects). One that is not is a form nobody has
    looked at, and fails rather than being stored where nothing looks it up.
    """

    def relative(path: str) -> str:
        if path == site_path:
            return "/"
        if path.startswith(site_path + "/"):
            return path[len(site_path) :]
        raise ValueError(f"redirect outside the site {site_path}: {path}")

    return [
        (relative(old), relative(new))
        for old, new in disk_raw.items()
        if base_raw.get(old) != new
    ]


# What a move itself changes: the item's path. Left out when asking whether a
# moved item's CONTENT changed too.
_PATH_FIELDS = ("@id", "id", "parent")


def _moved_key(data: dict[str, Any], rules: CompareRules) -> str:
    return content_key({k: v for k, v in data.items() if k not in _PATH_FIELDS}, rules)


def _without_state(data: dict[str, Any], rules: CompareRules) -> str:
    return content_key({k: v for k, v in data.items() if k != "review_state"}, rules)


def classify(  # noqa: C901
    previous: dict[str, dict],
    new: dict[str, dict],
    rules: CompareRules = NO_RULES,
) -> Plan:
    """What `new` changed against `previous`, the export the site was last
    deployed from. See the table in the README. `rules`: see
    cleaned_for_compare."""
    plan = Plan()

    # Moves first: same UID at a different path. A moved item is neither a
    # delete of the old path nor a create of the new one.
    previous_by_uid = _uid_index(previous)
    move_src: dict[str, str] = {}
    move_dst: dict[str, str] = {}
    for uid, rel_new in _uid_index(new).items():
        rel_old = previous_by_uid.get(uid)
        if rel_old is not None and rel_old != rel_new:
            move_src[rel_old] = rel_new
            move_dst[rel_new] = rel_old
            plan.moves.append(Move(src=rel_old, dst=rel_new, uid=uid))
            plan.redirects.append((rel_to_path(rel_old), rel_to_path(rel_new)))
            plan._action[rel_old] = Action.MOVE

    for rel in sorted(set(previous) | set(new)):
        if rel in move_src:
            continue
        if rel not in previous:
            if rel in move_dst:
                # The new path of a move: a change made in the same export is an
                # update of the new path, applied after the item is moved there.
                if _moved_key(previous[move_dst[rel]], rules) != _moved_key(
                    new[rel], rules
                ):
                    plan.updates.append(rel)
                    plan._action[rel] = Action.UPDATE
                continue
            plan.creates.append(rel)
            plan._action[rel] = Action.CREATE
            continue
        if rel not in new:
            plan.deletes.append(rel)
            plan._action[rel] = Action.DELETE
            continue
        if content_key(previous[rel], rules) == content_key(new[rel], rules):
            plan.noops.append(rel)
            plan._action[rel] = Action.NOOP
        elif _without_state(previous[rel], rules) == _without_state(new[rel], rules):
            plan.states.append(rel)
            plan._action[rel] = Action.STATE
        else:
            plan.updates.append(rel)
            plan._action[rel] = Action.UPDATE
    return plan


def conflicts(
    plan: Plan,
    previous: dict[str, dict],
    new: dict[str, dict],
    on_site: Mapping[str, Mapping[str, Any] | None],
    since: DateTime,
) -> dict[str, str]:
    """The items `plan` writes that an editor changed on the site after `since`,
    the last deploy, each with why.

    `on_site`: for each of `plan.written()`, its catalog entry's `modified` and
    `review_state`, or None when the site has no such item. A deploy's own
    writes are older than the time it records, so anything modified after it
    is an editor's. An item only changing state conflicts only with a change of
    its state: its content is not written.
    """
    found: dict[str, str] = {}
    for rel in plan.creates:
        if on_site.get(rel) is not None:
            found[rel] = "already on the site"
    states = set(plan.states)
    for rel in sorted(plan.written() - set(plan.creates)):
        entry = on_site.get(rel)
        if entry is None:
            if rel not in plan.deletes:
                found[rel] = "not on the site"
            continue
        # Publishing or retracting does not move the modification date, so a
        # state neither export gave the item is an editor's. An export that
        # declares no state says nothing about it.
        known = {previous[rel].get("review_state") if rel in previous else None}
        target = next((m.dst for m in plan.moves if m.src == rel), rel)
        if target in new:
            known.add(new[target].get("review_state"))
        known.discard(None)
        if known and entry["review_state"] not in known:
            found[rel] = "its state changed on the site"
        elif rel not in states and entry["modified"] > since:
            found[rel] = "changed on the site"
    return found


def load_tree(content_dir: Path) -> dict[str, dict]:
    """Walk a distribution content dir -> {rel_path: data.json}.

    Skips plone.app.iterate working copies (objects with a `working_copy_of`
    relation): they're drafts of a checked-out baseline, not deployable content.
    The live export emits them as real objects, so without this the classifier
    would see each draft as a new object to create/sync — leaking work-in-progress
    into git/prod. (The reverse `working_copy` field on the baseline is already in
    VOLATILE, so a checkout doesn't false-delta the baseline itself.)
    """
    out: dict[str, dict] = {}
    for data_file in content_dir.rglob("data.json"):
        data = json.loads(data_file.read_text())
        if data.get("working_copy_of"):
            continue
        rel = data_file.parent.relative_to(content_dir).as_posix()
        out[rel] = data
    return out
