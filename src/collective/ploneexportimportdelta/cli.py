"""`plone-importer-delta`: deploy an export to a site from the command line.

In the style of plone.exportimport's `plone-importer`: a zope.conf, the site
and the export. One of:

  --plan         what a deploy would change, as JSON; nothing is saved
  --commit       deploy it, in one transaction
  --record-base  after a full import, record the export as the site's base

The report is printed as JSON on stdout. The exit status is 1 when the deploy
is refused (no base yet, an editor's change it would overwrite) or did not
commit.
"""

from __future__ import annotations

from .delta import DeltaRefused
from .stored_base import deploy_content
from .stored_base import record_base
from pathlib import Path
from plone import api
from plone.exportimport.utils import cli as cli_helpers
from zope.component import hooks

import argparse
import json
import sys
import transaction


def parse_args(args: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="plone-importer-delta",
        description="Deploy a plone.exportimport export to a Plone site: "
        "only what changed, in one transaction",
    )
    parser.add_argument("zopeconf", help="Path to zope.conf")
    parser.add_argument("site", help="Plone site ID or path to the site")
    parser.add_argument("path", help="The export: the folder that holds content/")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--plan", action="store_true", help="Print what would change; save nothing"
    )
    mode.add_argument("--commit", action="store_true", help="Deploy it")
    mode.add_argument(
        "--record-base",
        action="store_true",
        help="Record the export as the site's base, after a full import",
    )
    parser.add_argument(
        "--seed-from-live",
        action="store_true",
        help="A site with no base starts from what it holds. The export then "
        "wins everywhere: for a site nobody edits",
    )
    parser.add_argument(
        "--ignore-field",
        action="append",
        default=[],
        metavar="FIELD",
        help="A field the site cannot hold, left out of the compare (repeatable)",
    )
    return parser.parse_args(args)


def run(site, request, args: argparse.Namespace) -> int:
    """The script once Zope is up. Returns the exit status."""
    export = Path(args.path)
    with hooks.site(site), api.env.adopt_roles(["Manager"]):
        if args.record_base:
            count = record_base(site, export)
            transaction.commit()
            print(json.dumps({"base_recorded": count}))
            return 0
        try:
            report = deploy_content(
                site,
                request,
                export,
                commit=args.commit,
                ignored_fields=frozenset(args.ignore_field),
                seed_from_live=args.seed_from_live,
            )
        except DeltaRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
    print(json.dumps(report, indent=2, default=str))
    if args.commit and not report["apply"]["committed"]:
        return 1
    return 0


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if not Path(args.path).is_dir():
        sys.exit(f"{args.path} is not a folder")
    logger = cli_helpers.get_logger("plone-importer-delta")
    app = cli_helpers.get_app(args.zopeconf)
    site = cli_helpers.get_site(app, args.site, logger)
    sys.exit(run(site, app.REQUEST, args))
