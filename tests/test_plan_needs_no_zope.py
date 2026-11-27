"""plone-delta-plan compares two exports on disk, so it runs anywhere: a CI
job installs the package with --no-deps, without Plone or Zope."""

from __future__ import annotations

import subprocess
import sys


def test_the_planner_imports_without_zope():
    # Any Zope or Plone module refuses to import, as on a plain Python.
    blocked = ["DateTime", "Products", "plone", "zope", "Zope2", "ZODB", "transaction"]
    code = (
        "import sys\n"
        f"for name in {blocked!r}: sys.modules[name] = None\n"
        "import collective.ploneexportimportdelta.plan\n"
    )
    result = subprocess.run(  # noqa: S603 -- the test's own code
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
