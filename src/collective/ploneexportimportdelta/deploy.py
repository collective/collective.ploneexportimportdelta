"""Deploy a new export to a site that was deployed from a previous one.

The caller passes both exports. The site records which export it was last
deployed from, and the previous export must be that one. classify works out
what the new export changed; conflicts, from the catalog, which of those items
an editor changed since; and only the changes are applied, in one transaction
that also records the new export. Nothing on the site is serialized.
"""

from __future__ import annotations

from .apply import apply_changeset
from .classify import classify
from .classify import CompareRules
from .classify import conflicts
from .classify import layout_redirects
from .classify import load_exportimport_tree
from .classify import SITE_ROOT
from .deployed import all_rels
from .deployed import changed_since
from .deployed import deployed
from .deployed import on_site
from .deployed import record
from collections.abc import Callable
from collections.abc import Iterable
from DateTime import DateTime
from pathlib import Path
from typing import Any

import json
import logging


logger = logging.getLogger(__name__)


class DeployRefused(Exception):
    """The deploy will not run, for a reason the caller must act on."""


def _topmost(rels: set[str]) -> list[str]:
    """`rels` without any inside another of them: deleting a folder takes what
    it holds."""
    return sorted(
        rel for rel in rels if not any(rel.startswith(other + "/") for other in rels)
    )


def _redirects(export: Path) -> dict:
    path = export / "redirects.json"
    return json.loads(path.read_text()) if path.exists() else {}


def deploy(  # noqa: C901
    site,
    previous: Path,
    new: Path,
    *,
    previous_id: str,
    new_id: str,
    commit: bool,
    overwrite: Iterable[str] = (),
    reset: bool = False,
    review_changeset: Callable[[dict[str, Any]], Any] | None = None,
    ignored_fields: frozenset[str] = frozenset(),
    with_relations: bool = True,
    whole_metadata: bool = False,
) -> dict[str, Any]:
    """Apply what `new` changed against `previous`; a dry run (everything runs,
    then is aborted) unless `commit`. `report["apply"]["committed"]` says
    whether anything landed.

    previous_id: the id of `previous`; it must be the export the site records.
    new_id: recorded with the content when it commits.

    overwrite: site paths to take from `new` even over an editor's change.
    Each is imported again whether or not the export changed it.

    reset: `new` wins everywhere, for a site that is only a copy of its export.
    What the site holds that neither export has is deleted, what both have that
    the site lost is made again, and what was changed on the site since the
    last deploy is taken back.

    review_changeset: the caller's look at the change-set before anything is
    applied; its return value goes in report["review"]. It refuses by raising.
    """
    record_now = deployed(site)
    if record_now is None:
        raise DeployRefused(
            "no export recorded on the site: import one in full, then "
            "record(site, its_id) in the same transaction"
        )
    if record_now["export"] != previous_id:
        raise DeployRefused(
            f"the site was last deployed from {record_now['export']!r}, not "
            f"{previous_id!r}: pass that export as the previous one"
        )
    since = DateTime(record_now["at"])
    previous_tree, _ = load_exportimport_tree(Path(previous) / "content")
    new_tree, new_dirs = load_exportimport_tree(Path(new) / "content")
    plan = classify(
        previous_tree, new_tree, CompareRules(ignored_fields=ignored_fields)
    )

    overwrite_rels = [path.strip("/") for path in overwrite]
    for rel in overwrite_rels:
        if rel not in new_tree:
            raise DeployRefused(f"overwrite {rel!r}: the new export has no such item")
    report: dict[str, Any] = {}
    taken_back: list[str] = []
    if reset:
        site_rels = all_rels(site)
        site_only = _topmost(site_rels - set(new_tree) - set(previous_tree))
        plan.deletes += [rel for rel in site_only if rel not in plan.deletes]
        arriving = {*plan.creates, *plan.updates, *(m.dst for m in plan.moves)}
        missing = sorted(
            rel
            for rel in set(new_tree) & set(previous_tree)
            if rel not in site_rels and rel != SITE_ROOT and rel not in arriving
        )
        for rel in missing:
            for bucket in (plan.noops, plan.states):
                if rel in bucket:
                    bucket.remove(rel)
            plan.creates.append(rel)
        taken_back = sorted(changed_since(site, since) & set(new_tree))
        report["reset"] = {
            "since": record_now["at"],
            "site_only": site_only,
            "missing": missing,
            "changed_on_site": taken_back,
        }
    for rel in [*overwrite_rels, *taken_back]:
        for bucket in (plan.noops, plan.states):
            if rel in bucket:
                bucket.remove(rel)
        if rel not in plan.updates and rel not in plan.creates:
            plan.updates.append(rel)

    found = conflicts(
        plan, previous_tree, new_tree, on_site(site, plan.written()), since
    )
    allowed = set(overwrite_rels) | set(taken_back)
    if reset:
        allowed |= set(found)
    report["conflicts"] = {rel: why for rel, why in found.items() if rel not in allowed}
    plan.warnings = sorted(report["conflicts"])

    plan.redirects += layout_redirects(
        _redirects(Path(new)),
        _redirects(Path(previous)),
        "/".join(site.getPhysicalPath()),
    )
    plan.base_tid = site._p_jar.db().storage.lastTransaction().hex()
    changeset = plan.to_changeset()
    if reset:
        changeset["site_only"] = report["reset"]["site_only"]
    report["plan"] = {k: v for k, v in changeset.items() if k != "warnings"}
    logger.info(
        "deploy %s -> %s: creates=%d updates=%d states=%d deletes=%d moves=%d "
        "conflicts=%d",
        previous_id,
        new_id,
        len(plan.creates),
        len(plan.updates),
        len(plan.states),
        len(plan.deletes),
        len(plan.moves),
        len(plan.warnings),
    )
    if review_changeset is not None:
        report["review"] = review_changeset(changeset)
    report["apply"] = apply_changeset(
        site,
        changeset,
        Path(new) / "content",
        dry_run=not commit,
        dir_for=new_dirs.__getitem__,
        with_relations=with_relations,
        whole_metadata=whole_metadata,
        before_commit=lambda: record(site, new_id),
    )
    return report
