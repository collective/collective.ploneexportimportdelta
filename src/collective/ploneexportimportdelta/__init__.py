"""Deploy a plone.exportimport layout to a live site incrementally.

Only what the layout changed is applied, in one transaction, so the site is
never missing while a deploy runs. stored_base.deploy_content is the entry
point; see the README.
"""

__version__ = "1.0.0a1.dev0"
