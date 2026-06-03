# Compatibility shim for the unit tests.
#
# The test-suite historically depended on the standalone ``mock`` package
# (mock-0.3.1) that was downloaded and dropped into the Wine site-packages by
# tools/check_wine. Since Python 3.3 the library is part of the standard
# library as ``unittest.mock``, so we simply re-export it here and let
# tools/check_wine copy this file into the Wine Python's site-packages.

from unittest.mock import *  # noqa: F401,F403
from unittest import mock as _mock

# Re-export everything (including names not covered by ``*``) so that
# ``import mock; mock.Mock`` keeps working exactly like the old package.
__all__ = getattr(_mock, '__all__', [])
for _name in dir(_mock):
    if not _name.startswith('__'):
        globals().setdefault(_name, getattr(_mock, _name))
del _name, _mock
