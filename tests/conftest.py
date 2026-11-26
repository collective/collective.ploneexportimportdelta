"""A Plone site for the tests: plone.restapi (the exporter serializes through
it), plone.volto (pages with blocks) and the package's ZCML, which registers
`@import-delta`; the server layer answers it over HTTP."""

from plone.app.contenttypes.testing import PLONE_APP_CONTENTTYPES_FIXTURE
from plone.app.testing import applyProfile
from plone.app.testing import FunctionalTesting
from plone.app.testing import IntegrationTesting
from plone.app.testing import PloneSandboxLayer
from plone.testing.zope import WSGI_SERVER_FIXTURE
from pytest_plone import fixtures_factory


class Layer(PloneSandboxLayer):
    defaultBases = (PLONE_APP_CONTENTTYPES_FIXTURE,)

    def setUpZope(self, app, configurationContext):
        import plone.exportimport
        import plone.restapi
        import plone.volto

        self.loadZCML(package=plone.restapi)
        self.loadZCML(package=plone.exportimport)
        self.loadZCML(package=plone.volto)
        import collective.ploneexportimportdelta

        self.loadZCML(package=collective.ploneexportimportdelta)

    def setUpPloneSite(self, portal):
        applyProfile(portal, "plone.volto:default")


FIXTURE = Layer()

INTEGRATION_TESTING = IntegrationTesting(
    bases=(FIXTURE,), name="PloneExportImportDelta:IntegrationTesting"
)
FUNCTIONAL_TESTING = FunctionalTesting(
    bases=(FIXTURE,), name="PloneExportImportDelta:FunctionalTesting"
)

SERVER_TESTING = FunctionalTesting(
    bases=(FIXTURE, WSGI_SERVER_FIXTURE), name="PloneExportImportDelta:Server"
)


pytest_plugins = ["pytest_plone"]


globals().update(
    fixtures_factory((
        (FUNCTIONAL_TESTING, "functional"),
        (INTEGRATION_TESTING, "integration"),
        (SERVER_TESTING, "server"),
    ))
)
