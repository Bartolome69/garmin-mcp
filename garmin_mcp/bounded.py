"""In-memory maps with a ceiling (Nov 25).

Every per-person or per-address map the server keeps in memory is one of
these: past `limit` entries, the one written longest ago goes. Each entry is
small (a time, a count), so the ceiling is about guarantee, not saving: no
map in the process can grow for ever, whatever traffic does.

Written-order, not read-order: a read never reorders, so code iterating a map
while it reads from it stays safe.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Iterable


class BoundedDict(OrderedDict):
    def __init__(self, limit: int, *args: Any, **kwargs: Any) -> None:
        self.limit = max(1, int(limit))
        self._lock = threading.RLock()
        super().__init__(*args, **kwargs)

    def __setitem__(self, key: Any, value: Any) -> None:
        with self._lock:
            if key in self:
                super().__delitem__(key)
            super().__setitem__(key, value)
            while len(self) > self.limit:
                self.popitem(last=False)

    def setdefault(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            if key in self:
                return self[key]
            self[key] = default
            return default

    def update(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def __reduce__(self):  # pragma: no cover - never pickled; keeps copy() sane
        return (self.__class__, (self.limit, list(self.items())))

    def copy(self) -> "BoundedDict":
        return BoundedDict(self.limit, self.items())


class BoundedSet:
    """A set with the same ceiling: the member added longest ago goes first."""

    def __init__(self, limit: int, items: Iterable[Any] = ()) -> None:
        self._items: BoundedDict = BoundedDict(limit)
        for item in items:
            self.add(item)

    def add(self, item: Any) -> None:
        self._items[item] = True

    def discard(self, item: Any) -> None:
        self._items.pop(item, None)

    def remove(self, item: Any) -> None:
        del self._items[item]

    def clear(self) -> None:
        self._items.clear()

    def __contains__(self, item: Any) -> bool:
        return item in self._items

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(list(self._items))
