from collections import OrderedDict
import threading
from typing import Any, Iterable, Iterator, Optional


class BoundedCache(OrderedDict):
    """
    Thread-safe bounded LRU dictionary cache.

    Subclasses OrderedDict so all standard dict operations (`cache[k] = v`,
    `cache.get(k)`, `k in cache`, `cache.clear()`) work seamlessly while
    automatically evicting the oldest entries once `maxsize` is exceeded.
    """

    def __init__(self, maxsize: int = 512, *args: Any, **kwargs: Any) -> None:
        self.maxsize = max(1, int(maxsize))
        self._lock = threading.RLock()
        super().__init__(*args, **kwargs)
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        while len(self) > self.maxsize:
            super().popitem(last=False)

    def __getitem__(self, key: Any) -> Any:
        with self._lock:
            value = super().__getitem__(key)
            super().move_to_end(key)
            return value

    def __setitem__(self, key: Any, value: Any) -> None:
        with self._lock:
            if key in self:
                super().move_to_end(key)
            super().__setitem__(key, value)
            self._evict_if_needed()

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            if key in self:
                value = super().__getitem__(key)
                super().move_to_end(key)
                return value
            return default

    def pop(self, key: Any, *args: Any) -> Any:
        with self._lock:
            return super().pop(key, *args)

    def clear(self) -> None:
        with self._lock:
            super().clear()


class BoundedSet:
    __class_getitem__ = classmethod(lambda cls, item: cls)
    """Thread-safe bounded FIFO/LRU set for tracking visited URLs or keys."""

    def __init__(self, maxsize: int = 1024, iterable: Optional[Iterable[Any]] = None) -> None:
        self.maxsize = max(1, int(maxsize))
        self._store: OrderedDict[Any, None] = OrderedDict()
        self._lock = threading.RLock()
        if iterable:
            for item in iterable:
                self.add(item)

    def add(self, item: Any) -> None:
        with self._lock:
            if item in self._store:
                self._store.move_to_end(item)
            else:
                self._store[item] = None
                while len(self._store) > self.maxsize:
                    self._store.popitem(last=False)

    def discard(self, item: Any) -> None:
        with self._lock:
            self._store.pop(item, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __contains__(self, item: Any) -> bool:
        with self._lock:
            return item in self._store

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def __iter__(self) -> Iterator[Any]:
        with self._lock:
            return iter(list(self._store.keys()))

