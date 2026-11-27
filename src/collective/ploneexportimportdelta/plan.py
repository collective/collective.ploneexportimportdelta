"""What to send `@import-delta`, from two exports: the one the site was last
deployed from and the new one. Runs anywhere; no site is needed.

The delta is a plone.exportimport export of the items to create, update or
move (a moved item at its new path: the site finds it by UID),
`deletes.json`, the UIDs of the items the new export no longer has, and
`states.json`, the UIDs of the items whose review state alone changed. Those
are in the export too, for their state, and the site transitions them instead
of importing them: an import reindexes an item's whole text, a PDF's included.

Both exports come from the same place, so any difference in an item's
data.json or its files is a change, and the item is sent whole. Nothing is
left out of the compare but the fields the caller names. The files the
import reads whole and applies without removing anything (relations,
principals, site settings) are copied as they are.
"""

from __future__ import annotations

from .changeset import build_subset_dir
from .changeset import ChangeSet
from .changeset import own_files
from .classify import load_exportimport_tree
from pathlib import Path
from typing import Any

import argparse
import json
import shutil
import sys


WHOLE_FILES = ("relations.json", "principals.json", "site-settings.json")


def plan_delta(
    previous: Path,
    new: Path,
    out: Path,
    *,
    ignored_fields: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Write the delta from `previous` to `new` into `out`, and return what it
    does, by path: creates, updates, states (review state alone changed),
    moves (sent at their new path, changed or not) and deletes. `ignored_fields` are left out of the compare."""
    previous_tree, previous_dirs = load_exportimport_tree(Path(previous) / "content")
    new_tree, new_dirs = load_exportimport_tree(Path(new) / "content")
    previous_by_uid = {data["UID"]: rel for rel, data in previous_tree.items()}
    new_uids = {data["UID"] for data in new_tree.values()}

    def item(tree, dirs, export, rel) -> tuple:
        folder = Path(export) / "content" / dirs[rel]
        data = {k: v for k, v in tree[rel].items() if k not in ignored_fields}
        files = [
            (name.as_posix(), (folder / name).read_bytes())
            for name in own_files(folder)
            if name.as_posix() != "data.json"
        ]
        return data, files

    def without_state(found: tuple) -> tuple:
        data, files = found
        return {k: v for k, v in data.items() if k != "review_state"}, files

    creates: list[str] = []
    updates: list[str] = []
    states: list[str] = []
    moves: list[dict[str, str]] = []
    for rel, data in new_tree.items():
        was = previous_by_uid.get(data["UID"])
        if was is None:
            creates.append(rel)
            continue
        if was != rel:
            moves.append({"src": was, "dst": rel})
            continue
        before = item(previous_tree, previous_dirs, previous, was)
        after = item(new_tree, new_dirs, new, rel)
        if before == after:
            continue
        if without_state(before) == without_state(after):
            states.append(rel)
        else:
            updates.append(rel)
    sent = sorted({*updates, *states, *(m["dst"] for m in moves)})
    build_subset_dir(
        ChangeSet(creates=sorted(creates), updates=sent),
        Path(new) / "content",
        Path(out),
        dir_for=new_dirs.__getitem__,
    )
    for name in WHOLE_FILES:
        if (Path(new) / name).exists():
            shutil.copy2(Path(new) / name, Path(out) / name)
    gone = sorted(
        rel for rel, data in previous_tree.items() if data["UID"] not in new_uids
    )
    deletes = [previous_tree[rel]["UID"] for rel in gone]
    (Path(out) / "deletes.json").write_text(json.dumps(deletes, indent=2) + "\n")
    state_uids = [new_tree[rel]["UID"] for rel in sorted(states)]
    (Path(out) / "states.json").write_text(json.dumps(state_uids, indent=2) + "\n")
    return {
        "creates": sorted(creates),
        "updates": sorted(updates),
        "states": sorted(states),
        "moves": sorted(moves, key=lambda m: m["dst"]),
        "deletes": gone,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="plone-delta-plan",
        description="Write what @import-delta is sent: the items that changed "
        "between two exports, and deletes.json",
    )
    parser.add_argument("previous", help="The export the site was last deployed from")
    parser.add_argument("new", help="The export to deploy")
    parser.add_argument("out", help="An empty folder for the delta")
    parser.add_argument(
        "--ignore-field",
        action="append",
        default=[],
        metavar="FIELD",
        help="A field left out of the compare (repeatable)",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        sys.exit(f"{out} is not empty")
    report = plan_delta(
        Path(args.previous),
        Path(args.new),
        out,
        ignored_fields=frozenset(args.ignore_field),
    )
    print(json.dumps(report, indent=2))
