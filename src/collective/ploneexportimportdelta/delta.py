"""Import a partial export into a site, with deletes, in one transaction.

The caller sends the items that changed (a plone.exportimport export holding
only those), the UIDs to delete, and the catalog counter it last saw. The
counter is Plone's `portal_catalog.getCounter()`, the number plone.app.caching
puts in its ETags: every catalog write adds one, so any content change moves
it, and an image scale or a login does not. If the site's counter is not the
caller's, something changed since the caller's export: nothing is written, and
the caller exports, merges and tries again.

What happens to each item is read from the site, by UID: a UID the site does
not have is created, one at the same path is updated, one at another path is
moved there and updated. Only deletes cannot be read from a partial export (an
item it leaves out is one that did not change), so the caller names them.
"""

from __future__ import annotations

from .apply import apply_changeset
from .classify import load_exportimport_tree
from .classify import SITE_ROOT
from collections.abc import Iterable
from pathlib import Path
from plone import api
from typing import Any


class StaleCounter(Exception):
    """The site changed since the counter the caller sent."""

    def __init__(self, sent: int, current: int):
        super().__init__(
            f"the site changed since counter {sent} (it is now {current}): "
            "export it, merge, and deploy again"
        )
        self.sent = sent
        self.current = current


def site_counter(site) -> int:
    """The catalog counter, after any queued indexing."""
    return api.portal.get_tool("portal_catalog").getCounter()


def _rel_for_uid(site, uid: str) -> str | None:
    """Where the site holds `uid`, relative to the site; None if it does not."""
    brains = api.portal.get_tool("portal_catalog").unrestrictedSearchResults(UID=uid)
    if not brains:
        return None
    portal_path = "/".join(site.getPhysicalPath())
    return brains[0].getPath()[len(portal_path) :].lstrip("/")


def plan(
    site, tree: dict[str, dict], deletes: Iterable[str], states: Iterable[str] = ()
) -> dict[str, Any]:
    """The change-set for a partial export, read from the site by UID. An item
    whose UID is in `states` is transitioned, not imported."""
    creates: list[str] = []
    updates: list[str] = []
    state_only: list[str] = []
    moves: list[dict[str, str]] = []
    state_uids = set(states)
    for rel, data in tree.items():
        if rel == SITE_ROOT:
            updates.append(rel)
            continue
        uid = data["UID"]
        on_site = _rel_for_uid(site, uid)
        if uid in state_uids:
            if on_site != rel:
                raise LookupError(
                    f"state change for {uid}: the site has it at {on_site}, not {rel}"
                )
            state_only.append(rel)
            continue
        if on_site is None:
            creates.append(rel)
            continue
        if on_site != rel:
            moves.append({"src": on_site, "dst": rel, "uid": uid})
        updates.append(rel)
    deleted: list[str] = []
    for uid in deletes:
        on_site = _rel_for_uid(site, uid)
        if on_site is None:
            raise LookupError(f"delete {uid}: the site has no such item")
        deleted.append(on_site)
    missing = state_uids - {tree[rel]["UID"] for rel in state_only}
    if missing:
        raise LookupError(f"state change for {sorted(missing)}: not in the export")
    return {
        "creates": sorted(creates),
        "updates": sorted(updates),
        "states": sorted(state_only),
        "deletes": sorted(deleted),
        "moves": moves,
    }


def import_delta(
    site,
    export: Path,
    *,
    deletes: Iterable[str],
    counter: int | None,
    states: Iterable[str] = (),
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a partial export and `deletes` (UIDs) in one transaction; abort
    it instead when `dry_run`. The items whose UIDs are in `states` are only
    transitioned to the review_state their data.json declares.

    counter: the catalog counter the caller's export was taken at; raises
    StaleCounter, writing nothing, if the site's is another. None applies
    regardless, for a site that is only a copy of its export.

    The report's "counter" is the one to send with the next deploy: the
    site's after this one commits (the one it checked, on a dry run).
    """
    current = site_counter(site)
    if counter is not None and counter != current:
        raise StaleCounter(counter, current)
    tree, dirs = load_exportimport_tree(Path(export) / "content")
    changeset = plan(site, tree, deletes, states)
    # The engine's own check that nothing changed while it was planned: here
    # planning and applying are one transaction.
    changeset["base_tid"] = site._p_jar.db().storage.lastTransaction().hex()
    report: dict[str, Any] = {"plan": changeset, "counter": current}

    def after_writes() -> None:
        report["counter"] = site_counter(site)

    report["apply"] = apply_changeset(
        site,
        changeset,
        Path(export) / "content",
        dry_run=dry_run,
        dir_for=dirs.__getitem__,
        before_commit=after_writes,
    )
    report["committed"] = report["apply"]["committed"]
    return report
