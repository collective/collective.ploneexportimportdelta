"""`plone-importer-delta`: deploy an export to a site from the command line.

In the style of plone.exportimport's `plone-importer`: a zope.conf and the
site, then one of:

  --show         print the export the site was last deployed from, as JSON
  --plan         what a deploy would change, as JSON; nothing is saved
  --commit       deploy it, in one transaction
  --record ID    after a full import, record ID as the export the site holds

--plan and --commit take both exports: --previous (the one --show names) with
--previous-id, and --new with --new-id. The exit status is 1 when the deploy
is refused or did not commit, with the reason on stderr or in the report.
"""

from __future__ import annotations

from .deploy import deploy
from .deploy import DeployRefused
from .deployed import deployed
from .deployed import record
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
        description="Deploy a plone.exportimport export to a Plone site: only "
        "what changed since the export it was last deployed from",
    )
    parser.add_argument("zopeconf", help="Path to zope.conf")
    parser.add_argument("site", help="Plone site ID or path to the site")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--show",
        action="store_true",
        help="Print the export the site was last deployed from",
    )
    mode.add_argument(
        "--plan", action="store_true", help="Print what would change; save nothing"
    )
    mode.add_argument("--commit", action="store_true", help="Deploy it")
    mode.add_argument(
        "--record",
        metavar="ID",
        help="After a full import, record ID as the export the site holds",
    )
    parser.add_argument("--previous", help="The export the site was last deployed from")
    parser.add_argument("--previous-id", help="Its id, as --show prints it")
    parser.add_argument("--new", help="The export to deploy")
    parser.add_argument("--new-id", help="Its id, recorded when it commits")
    parser.add_argument(
        "--overwrite",
        action="append",
        default=[],
        metavar="PATH",
        help="A page to take from the new export even over an editor's change "
        "(repeatable)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="The new export wins everywhere: for a site that is only a copy of it",
    )
    parser.add_argument(
        "--ignore-field",
        action="append",
        default=[],
        metavar="FIELD",
        help="A field left out of the compare (repeatable)",
    )
    namespace = parser.parse_args(args)
    if namespace.plan or namespace.commit:
        missing = [
            option
            for option, value in (
                ("--previous", namespace.previous),
                ("--previous-id", namespace.previous_id),
                ("--new", namespace.new),
                ("--new-id", namespace.new_id),
            )
            if not value
        ]
        if missing:
            parser.error(f"a deploy needs {', '.join(missing)}")
    return namespace


def run(site, args: argparse.Namespace) -> int:
    """The script once Zope is up. Returns the exit status."""
    with hooks.site(site), api.env.adopt_roles(["Manager"]):
        if args.show:
            print(json.dumps(deployed(site)))
            return 0
        if args.record:
            record(site, args.record)
            transaction.commit()
            print(json.dumps(deployed(site)))
            return 0
        try:
            report = deploy(
                site,
                Path(args.previous),
                Path(args.new),
                previous_id=args.previous_id,
                new_id=args.new_id,
                commit=args.commit,
                overwrite=args.overwrite,
                reset=args.reset,
                ignored_fields=frozenset(args.ignore_field),
            )
        except DeployRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
    print(json.dumps(report, indent=2, default=str))
    if args.commit and not report["apply"]["committed"]:
        return 1
    return 0


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    for export in (args.previous, args.new):
        if export is not None and not Path(export).is_dir():
            sys.exit(f"{export} is not a folder")
    logger = cli_helpers.get_logger("plone-importer-delta")
    app = cli_helpers.get_app(args.zopeconf)
    site = cli_helpers.get_site(app, args.site, logger)
    sys.exit(run(site, args))
