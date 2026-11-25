# collective.ploneexportimportdelta

Deploy a [plone.exportimport](https://github.com/plone/plone.exportimport) export to a running Plone site incrementally. Only what the export changed is applied, in one transaction, and an editor's change on the site is never overwritten without being asked.

A site whose content ships with its code is often deployed by deleting the site and importing the export again. While that import runs there is no site: every page is a 404 to visitors, on every machine that shares the database. This package applies only the difference, so visitors see the old content until the new content is complete.

It needs Plone 6.2 (plone.exportimport 2.3.0 or later). It is a library that a deploy script calls inside the Plone process. It has no ZCML, profile or views, so a site never loads it.

## What a deploy does

- **Creates and updates** go through plone.exportimport's own importer, for just the changed items.
- **A change of review state alone** is a workflow transition. The item is not imported again, so its text, a PDF's included, is not indexed again.
- **Moves** keep the item's UID, history and place in its folder, and leave a redirect from the old path.
- **Deletes** remove the item, and its address answers 404. To send a deleted page's visitors somewhere else, add the redirect to `redirects.json`.
- **Ordering, default pages, relations, workflow states and redirects** from the export are applied too.

## Two exports and a record

A deploy compares two exports: the one the site was last deployed from, and the new one. The caller passes both. The site keeps only a record of which export it was last deployed from (an id the caller chooses: a git commit, a bucket object's name) and when, written in the same transaction as the content. So the record is always the export the site holds: a dry run, a failure or a refused deploy leaves both as they were.

Nothing on the site is serialized or compared with the export. What changed comes from the two exports; whether an editor changed something comes from the catalog.

## What changed, and what an editor changed

| The new export, against the previous one | Result |
|---|---|
| unchanged | nothing |
| changed | updated |
| only its review state changed | transitioned, not imported again |
| new | created |
| gone | deleted |
| same UID at a new path | moved, with a redirect from the old path |

Fields that are not content (modification dates, versions, workflow history, what the importer itself stamps) are left out of the compare. So are values that only spell "unset" differently: `null`, `""`, `[]`.

Of the items a deploy writes, it refuses (lists under `conflicts`, writes nothing) any that an editor changed on the site since the last deploy:

- modified after the recorded time: the catalog's own modification date;
- in a review state neither export gave it (publishing or retracting does not move the modification date);
- gone from the site, when the deploy would update or move it;
- already on the site, when the deploy would create it.

An edit to an item the new export does not change is kept, and is no conflict. Changes that move neither date nor state, such as sharing, are not seen.

Before it commits, a deploy also checks the site's transaction log, and refuses if an editor saved anything after the plan was made.

## From the command line

In the style of plone.exportimport's `plone-importer`: a `zope.conf` and the site.

```sh
# Which export is the site on?
plone-importer-delta instance/etc/zope.conf Plone --show
# What would deploying the new one change?
plone-importer-delta instance/etc/zope.conf Plone --plan \
    --previous export-a1b2c3/ --previous-id a1b2c3 \
    --new export-d4e5f6/ --new-id d4e5f6
# Deploy it.
plone-importer-delta instance/etc/zope.conf Plone --commit \
    --previous export-a1b2c3/ --previous-id a1b2c3 \
    --new export-d4e5f6/ --new-id d4e5f6
```

- `--show` prints the export the site was last deployed from, as JSON. Fetch that one as `--previous`.
- `--plan` prints what a deploy would change, as JSON, and saves nothing.
- `--commit` deploys it, in one transaction, and records `--new-id`.
- `--record ID` records `ID` as the export the site holds, after a full import with `plone-importer`.
- `--overwrite PATH` takes a page from the new export even over an editor's change. It can be given more than once.
- `--reset`: the new export wins everywhere (see below).
- `--ignore-field FIELD` leaves a field out of the compare. It can be given more than once.

The report is printed as JSON. The exit status is 1 when the deploy is refused or did not commit.

It runs wherever Zope can start with the site's `zope.conf`: over ssh, with `docker exec`, or in a one-off container. There is no web endpoint for it: a deploy can take many minutes, which is longer than a request should hold a server worker.

## From Python

```python
from collective.ploneexportimportdelta.deploy import deploy
from collective.ploneexportimportdelta.deployed import deployed
from collective.ploneexportimportdelta.deployed import record

# First deploy: import the export in full, as you do now, then
record(site, "a1b2c3")
transaction.commit()

# Every deploy after: fetch the export deployed(site)["export"] names.
report = deploy(
    site, previous, new, previous_id="a1b2c3", new_id="d4e5f6", commit=True
)
```

An export is the folder that holds `content/`, and `redirects.json`, `relations.json`, `principals.json` or `site-settings.json` if it has them. `report["apply"]["committed"]` says whether anything landed. A deploy whose previous export is not the one the site records is refused.

A site that is only a copy of its export (one being tried out before it goes live, say) can have the export win everywhere: `deploy(..., reset=True)`. Pages made on the site are deleted, pages deleted on the site are made again, and every page changed on the site since the last deploy is taken back from the export. The report lists what a reset undid under `reset`, and a caller's `review_changeset` sees the deletions as `site_only`.

## Upstream

Most of this belongs in plone.exportimport: importing only some paths, deleting and moving items, and a plan of what an import would change. The intent is to offer those there in small pieces, and for this package to shrink to what is left.

## Development

```sh
uv venv
uv pip install -c https://dist.plone.org/release/6.2.3/constraints.txt -e '.[test]'
.venv/bin/pytest
```

Moved from [Inka](https://github.com/Inkaengine/inka) (`inkaengine.inka.content_deploy`), with its history.
