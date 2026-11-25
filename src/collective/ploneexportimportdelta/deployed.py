"""What the site knows about its deploys: which export it was last deployed
from, and when. And what the catalog says about the items a deploy writes.

Nothing here serializes content: an item's modification date and review state
come from its catalog entry.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from DateTime import DateTime
from persistent.mapping import PersistentMapping
from typing import Any
from zope.annotation.interfaces import IAnnotations


KEY = "collective.ploneexportimportdelta.deployed"


def deployed(site) -> dict[str, str] | None:
    """{"export": <id>, "at": <ISO time>} of the last deploy, or None."""
    stored = IAnnotations(site).get(KEY)
    return dict(stored) if stored is not None else None


def record(site, export_id: str) -> None:
    """Record that the site now holds `export_id`, as of now.

    Writes, never commits: call it in the transaction that put the content
    there, after its last write. A full import's caller records the export it
    imported; a deploy records its own just before it commits.
    """
    if not export_id:
        raise ValueError("an export needs an id to be recorded")
    # To the microsecond: whole seconds would take the deploy's own writes, in
    # the same second, for an editor's on the next deploy.
    IAnnotations(site)[KEY] = PersistentMapping(
        export=export_id, at=datetime.now(timezone.utc).isoformat()
    )


def _rel(site, path: str) -> str:
    prefix = "/".join(site.getPhysicalPath())
    return path[len(prefix) :].strip("/")


def on_site(site, rels: set[str]) -> dict[str, dict[str, Any] | None]:
    """For each of `rels`, its catalog entry's `modified` and `review_state`,
    or None when the site has no such item. Unrestricted: what is there, not
    what the deploying user may see."""
    prefix = "/".join(site.getPhysicalPath())
    found: dict[str, dict[str, Any] | None] = dict.fromkeys(rels)
    paths = [f"{prefix}/{rel}" for rel in rels]
    if not paths:
        return found
    for brain in site.portal_catalog.unrestrictedSearchResults(
        path={"query": paths, "depth": 0}
    ):
        found[_rel(site, brain.getPath())] = {
            "modified": brain.modified,
            "review_state": brain.review_state,
        }
    return found


def all_rels(site) -> set[str]:
    """Every content path the site holds, as an export spells it."""
    prefix = "/".join(site.getPhysicalPath())
    return {
        rel
        for brain in site.portal_catalog.unrestrictedSearchResults(path=prefix)
        if (rel := _rel(site, brain.getPath()))
    }


def changed_since(site, since: DateTime) -> set[str]:
    """The content paths modified after `since`. The date index keeps minutes,
    so it narrows the search and each entry's own date decides."""
    prefix = "/".join(site.getPhysicalPath())
    return {
        rel
        for brain in site.portal_catalog.unrestrictedSearchResults(
            path=prefix, modified={"query": since, "range": "min"}
        )
        if brain.modified > since and (rel := _rel(site, brain.getPath()))
    }
