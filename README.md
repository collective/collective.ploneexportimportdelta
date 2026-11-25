# collective.ploneexportimportdelta

Deploy a [plone.exportimport](https://github.com/plone/plone.exportimport) export to a running Plone site incrementally. Only what the export changed is applied, in one transaction, and an editor's change on the site is never overwritten without being asked.

A site whose content ships with its code is often deployed by deleting the site and importing the export again. While that import runs there is no site: every page is a 404 to visitors, on every machine that shares the database. This package applies only the difference, so visitors see the old content until the new content is complete.

It is a library that a deploy script calls inside the Plone process. It has no ZCML, profile or views, so a site never loads it.

## What a deploy does

- **Creates and updates** go through plone.exportimport's own importer, for just the changed items.
- **A change of review state alone** is a workflow transition. The item is not imported again, so its text, a PDF's included, is not indexed again.
- **Moves** keep the item's UID, history and place in its folder, and leave a redirect from the old path.
- **Deletes** remove the item, and its address answers 404. To send a deleted page's visitors somewhere else, add the redirect to `redirects.json`.
- **Ordering, default pages, relations, workflow states and redirects** from the export are applied too.

The site keeps its own base: the export it was last deployed from, saved in the same transaction as the content. A dry run, a failure or a refused deploy leaves both the content and the base as they were.

## How each item is classified

Each item is compared three ways: the base (the export last deployed), the new export, and the site as it is now.

| The export, against the base | The site, against the base | Result |
|---|---|---|
| unchanged | unchanged | nothing |
| unchanged | changed | left alone: an editor's change is kept |
| changed | unchanged | updated |
| changed | changed the same way | updated |
| changed | changed another way | refused: listed under `must_merge` |
| only its review state changed | any | transitioned; refused only if an editor changed the state |
| new | | created |
| gone | | deleted |
| same UID at a new path | | moved, with a redirect from the old path |

Fields that are not content (modification dates, versions, workflow history, what the importer itself stamps) are left out of the compare. So are values that only spell "unset" differently: `null`, `""`, `[]`, a field at its type's default, an item on its type's default view.

Before it commits, a deploy also checks the site's transaction log, and refuses if an editor saved anything after the plan was made.

## Use

```python
from collective.ploneexportimportdelta.stored_base import deploy_content
from collective.ploneexportimportdelta.stored_base import has_base
from collective.ploneexportimportdelta.stored_base import record_base

if has_base(site):
    report = deploy_content(site, request, export, commit=True)
else:
    # First deploy: import the export in full, as you do now, then
    record_base(site, export)
    transaction.commit()
```

`export` is the folder that holds `content/`, and `redirects.json`, `relations.json`, `principals.json` or `site-settings.json` if the export has them. `report["apply"]["committed"]` says whether anything landed.

A site that already holds its content, and that nobody edits, can skip that full import: `deploy_content(site, request, export, commit=True, seed_from_live=True)` starts from what the site holds. The export then wins everywhere, as a full import would, so an editor's change is overwritten and content the export lacks is deleted. A site that has a base keeps it.

A field your site's schema cannot hold looks like an edit on every page that has it, because no export can carry it back. Name such fields in `ignored_fields`.

A site that is only a copy of its export (one being tried out before it goes live, say) can have the export win everywhere on every deploy instead: `run_delta(..., reset=True)` from `collective.ploneexportimportdelta.delta`. Pages made on the site are deleted, pages deleted on the site are made again, and every page changed on the site since the last committed deploy is taken back from the export. Each committed deploy records when it was classified, and a reset counts changes from then. A site with no deploy on record needs `changed_since`, the time to count from. The report lists what a reset undid under `reset`, and a caller's `review_changeset` sees the deletions as `site_only`.

## Upstream

Most of this belongs in plone.exportimport: importing only some paths, deleting and moving items, and a plan of what an import would change. The intent is to offer those there in small pieces, and for this package to shrink to what is left.

## Development

```sh
uv venv
uv pip install -c https://dist.plone.org/release/6.2.3/constraints.txt -e '.[test]'
.venv/bin/pytest
```

Moved from [Inka](https://github.com/Inkaengine/inka) (`inkaengine.inka.content_deploy`), with its history.
