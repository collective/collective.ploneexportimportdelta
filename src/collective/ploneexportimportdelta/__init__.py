"""Deploy a plone.exportimport export to a site incrementally.

The caller passes the export the site was last deployed from and the new one;
only what changed between them is applied, in one transaction, and nothing an
editor changed since is overwritten. deploy.deploy is the entry point; see the
README.
"""

__version__ = "1.0.0a1.dev0"
