"""`@import-delta`: import_delta over REST, beside plone.exportimport's `@import`.

GET answers the catalog counter. POST takes a partial export as a zip in
`file` (as `@import` does), `deletes` (a JSON list of UIDs), `dry_run`, and
either the `counter` the caller's export was taken at or `force`. A counter
the site has moved past is a 409 that answers the site's current one.
"""

from __future__ import annotations

from .delta import import_delta
from .delta import site_counter
from .delta import StaleCounter
from io import BytesIO
from plone.exportimport.utils import zip_utils
from plone.restapi.services import Service
from typing import Any

import json
import shutil


def _flag(value: str | None) -> bool:
    return value in ("true", "1")


class CounterGet(Service):
    def reply(self) -> dict[str, int]:
        return {"counter": site_counter(self.context)}


class ImportDeltaPost(Service):
    def _error(self, status: int, error: str, **more: Any) -> dict[str, Any]:
        self.request.response.setStatus(status)
        return {"error": error, **more}

    def reply(self) -> dict[str, Any]:
        form = self.request.form
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return self._error(400, "no file: send the partial export as a zip")
        counter = form.get("counter")
        force = _flag(form.get("force"))
        if (counter is None) == (not force):
            return self._error(
                400,
                "send either the counter the export was taken at, or force "
                "to apply whatever the site changed since",
            )
        deletes = json.loads(form.get("deletes", "[]"))
        export = zip_utils.import_from_zip(BytesIO(upload.read()))
        try:
            return import_delta(
                self.context,
                export,
                deletes=deletes,
                counter=None if force else int(counter),
                dry_run=_flag(form.get("dry_run")),
            )
        except StaleCounter as exc:
            return self._error(409, str(exc), counter=exc.current)
        finally:
            shutil.rmtree(export)
