"""Python 3 compatibility shim for the removed Py2 ``sets`` module.

The original ``sets`` module (and its ``Set`` / ``ImmutableSet`` classes)
was removed in Python 3 in favour of the built-in ``set`` and
``frozenset`` types.  This shim maps the old names onto the built-ins so
that legacy ``from sets import Set`` style imports keep working.
"""

Set = set
ImmutableSet = frozenset
BaseSet = set

__all__ = ['BaseSet', 'Set', 'ImmutableSet']
