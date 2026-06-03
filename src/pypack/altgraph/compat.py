"""
Compatibility helpers.

Originally a Python 2.4-like compatibility library for Python 2.3; ported to
Python 3.  Under Python 3 most of these names are builtins or live in
``itertools``/``operator``/``collections``; the Python 2 aliases (``imap``,
``izip``, ``ifilter``, ``xrange``) are provided here so that the rest of the
package keeps working unchanged.
"""
from itertools import count, repeat, tee, groupby

# Python 2 names that were removed in Python 3.
imap = map
izip = zip
xrange = range

from itertools import filterfalse as _filterfalse  # noqa: F401


def ifilter(predicate, iterable):
    return filter(predicate, iterable)


from operator import attrgetter, itemgetter  # noqa: E402,F401

from collections import deque  # noqa: E402,F401

#
# new functions
#
import heapq as _heapq


def isorted(iterable):
    lst = list(iterable)
    _heapq.heapify(lst)
    pop = _heapq.heappop
    while lst:
        yield pop(lst)


def ireversed(iterable):
    if isinstance(iterable, (list, tuple)):
        for i in range(len(iterable) - 1, -1, -1):
            yield iterable[i]
    for obj in reversed(iterable):
        yield obj
