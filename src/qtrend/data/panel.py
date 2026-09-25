"""The hourly UTC price panel and the hard data boundary around it.

This module is the whole of README section 7.2 rule 2, made mechanical: a strategy function
receives a `PanelView` and is *physically incapable* of reading a row it should not see.

Two objects:

* `Panel` owns the data. Its arrays are private. It is handed to the simulator and to the
  look-ahead checker, never to strategy code.
* `PanelView` is what strategy code gets. It is a window over rows `[0, stop)` of a panel
  and exposes eight accessors, none of which takes an absolute row index or a timestamp
  beyond `end_time`.

### Why the accessors are relative, never absolute

A `PanelView` deliberately has no `row(i)`, no `at(timestamp)` and no way to ask for
anything past its own end. Every offset is counted backwards from `end_time`. If a strategy
author *can* reach past the end of the view, eventually one will, and the resulting backtest
looks better rather than crashing (CLAUDE.md rule 4). Adding an accessor here is a change to
the safety property this whole system rests on, not a convenience.

### Why `slice_to` never copies

`slice_to` is called once per simulated hour. Copying a 100k x 50 price array each time
would be slow, but the reason it returns a view is correctness, not speed: a copy taken at
`t` and handed to a strategy could be mutated by that strategy without the panel noticing,
and the look-ahead checker's perturbation would no longer be visible through it. Accessors
return read-only views, so a strategy cannot write through the boundary either.

### Close-time indexing

The index is the *close* time of each bar: the moment its close price became known
(README section 7.1 rule 1). A bar covering 00:00-01:00 is indexed at 01:00 and is invisible
to `slice_to(00:30)`, because at 00:30 nobody knew its close. `Panel.from_bars` builds the
index from open times so this conversion happens in one place.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

_NS_PER_SECOND = 1_000_000_000


def _as_datetime64(value: object) -> np.datetime64:
    """Convert an aware (or UTC-assumed naive) datetime to a nanosecond datetime64."""
    if isinstance(value, np.datetime64):
        return value.astype("datetime64[ns]")
    if not isinstance(value, datetime):
        raise TypeError(f"expected a datetime, got {type(value).__name__}")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return np.datetime64(value.astimezone(UTC).replace(tzinfo=None), "ns")


def _as_datetime(value: np.datetime64) -> datetime:
    """Convert a datetime64 back to an aware UTC datetime."""
    return value.astype("datetime64[us]").astype(datetime).replace(tzinfo=UTC)


class Panel:
    """An hourly UTC panel of close prices, quote volumes and a stale mask.

    Arrays are `(n_rows, n_symbols)`. The index is close time, strictly ascending, on a
    uniform grid. Symbols are sorted at construction so that every downstream iteration is
    deterministic (CLAUDE.md rule 8).

    The four data attributes are private and only `PanelView` reads them.
    """

    __slots__ = (
        "_index",
        "_close",
        "_quote_volume",
        "_stale",
        "_symbols",
        "_step",
        "_column_of",
    )

    def __init__(
        self,
        *,
        index: object,
        close: object,
        quote_volume: object,
        stale: object,
        symbols: object,
    ) -> None:
        symbol_tuple = tuple(symbols)
        if len(symbol_tuple) == 0:
            raise ValueError("panel needs at least one symbol")
        if any(not isinstance(symbol, str) for symbol in symbol_tuple):
            raise TypeError("symbols must be strings")
        if len(set(symbol_tuple)) != len(symbol_tuple):
            raise ValueError("symbols must be unique")

        index_array = self._build_index(index)
        n_rows = index_array.shape[0]
        n_symbols = len(symbol_tuple)

        close_array = np.asarray(close, dtype=np.float64)
        volume_array = np.asarray(quote_volume, dtype=np.float64)
        stale_array = np.asarray(stale, dtype=bool)

        for name, array in (
            ("close", close_array),
            ("quote_volume", volume_array),
            ("stale", stale_array),
        ):
            if array.shape != (n_rows, n_symbols):
                raise ValueError(
                    f"{name} has shape {array.shape}, expected {(n_rows, n_symbols)}"
                )

        # Sort symbols and permute the columns to match, so column order is a function of
        # the symbol names alone and never of the caller's argument order.
        order = np.argsort(np.asarray(symbol_tuple, dtype=object), kind="stable")
        self._symbols: tuple[str, ...] = tuple(symbol_tuple[i] for i in order)
        self._index = index_array
        self._close = np.ascontiguousarray(close_array[:, order])
        self._quote_volume = np.ascontiguousarray(volume_array[:, order])
        self._stale = np.ascontiguousarray(stale_array[:, order])
        self._step = self._uniform_step(index_array)
        self._column_of = {symbol: i for i, symbol in enumerate(self._symbols)}

    @staticmethod
    def _build_index(index: object) -> np.ndarray:
        values = list(index) if not isinstance(index, np.ndarray) else index
        if isinstance(values, np.ndarray) and values.dtype.kind == "M":
            array = values.astype("datetime64[ns]")
        else:
            array = np.array([_as_datetime64(value) for value in values], dtype="datetime64[ns]")
        if array.ndim != 1:
            raise ValueError("index must be one-dimensional")
        if array.shape[0] == 0:
            raise ValueError("panel needs at least one row")
        if not np.all(np.diff(array) > np.timedelta64(0, "ns")):
            raise ValueError("index must be strictly increasing close times")
        return array

    @staticmethod
    def _uniform_step(index: np.ndarray) -> np.timedelta64:
        """Bars sit on a uniform grid: missing data is forward-filled and flagged stale.

        README section 7.1 rule 3 fills gaps rather than dropping rows, so a non-uniform
        index means the panel was built wrong -- and `price_at_offset` would silently mean
        something different per row.
        """
        if index.shape[0] == 1:
            return np.timedelta64(3_600, "s").astype("timedelta64[ns]")
        deltas = np.diff(index)
        if not np.all(deltas == deltas[0]):
            raise ValueError(
                "index is not on a uniform grid; forward-fill missing bars and flag them "
                "stale rather than omitting rows"
            )
        return deltas[0]

    @classmethod
    def from_bars(
        cls,
        *,
        open_times: object,
        interval: timedelta,
        close: object,
        quote_volume: object,
        stale: object,
        symbols: object,
    ) -> Panel:
        """Build a panel from bars labelled by *open* time.

        The index becomes `open_time + interval`, the moment the close was known. This is
        the single place the open-to-close conversion happens (README section 7.1 rule 1).
        """
        if not isinstance(interval, timedelta):
            raise TypeError(f"interval must be a timedelta, got {type(interval).__name__}")
        if interval <= timedelta(0):
            raise ValueError("interval must be positive")
        step = np.timedelta64(int(interval.total_seconds()) * _NS_PER_SECOND, "ns")
        opens = np.array([_as_datetime64(value) for value in open_times], dtype="datetime64[ns]")
        return cls(
            index=opens + step,
            close=close,
            quote_volume=quote_volume,
            stale=stale,
            symbols=symbols,
        )

    # -- shape -------------------------------------------------------------------------

    @property
    def n_rows(self) -> int:
        return int(self._index.shape[0])

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    @property
    def step(self) -> timedelta:
        return timedelta(seconds=int(self._step.astype("timedelta64[s]").astype(np.int64)))

    @property
    def start_time(self) -> datetime:
        return _as_datetime(self._index[0])

    @property
    def end_time(self) -> datetime:
        return _as_datetime(self._index[-1])

    # -- the boundary ------------------------------------------------------------------

    def slice_to(self, t: datetime) -> PanelView:
        """Every row whose close time is `<= t`. Allocates no array (README section 9.3)."""
        stop = int(np.searchsorted(self._index, _as_datetime64(t), side="right"))
        return PanelView(self, stop)

    # -- simulator-only accessors ------------------------------------------------------
    #
    # These read rows the strategy cannot see. That is deliberate and it is why they live
    # on Panel and not on PanelView: the simulator fills a decision made at `t` using the
    # NEXT bar's price (README section 9.3 point 4, CLAUDE.md rule 5).

    def timestamps_between(self, start: datetime, end: datetime) -> tuple[datetime, ...]:
        """Grid close times in `[start, end]`, ascending."""
        lo = int(np.searchsorted(self._index, _as_datetime64(start), side="left"))
        hi = int(np.searchsorted(self._index, _as_datetime64(end), side="right"))
        return tuple(_as_datetime(value) for value in self._index[lo:hi])

    def next_timestamp(self, t: datetime) -> datetime:
        """The first grid close time strictly after `t`."""
        position = int(np.searchsorted(self._index, _as_datetime64(t), side="right"))
        if position >= self.n_rows:
            raise IndexError(f"no bar after {t.isoformat()}; the panel ends at {self.end_time}")
        return _as_datetime(self._index[position])

    def prices_at(self, t: datetime) -> dict[str, float]:
        """Close prices at exactly `t`. Raises unless `t` is on the grid."""
        target = _as_datetime64(t)
        position = int(np.searchsorted(self._index, target, side="left"))
        if position >= self.n_rows or self._index[position] != target:
            raise KeyError(f"{t.isoformat()} is not a bar close time in this panel")
        row = self._close[position]
        return {symbol: float(row[i]) for i, symbol in enumerate(self._symbols)}

    # -- look-ahead checker support (README section 7.2) --------------------------------

    def copy(self) -> Panel:
        """A deep copy. The checker perturbs the copy, never the original."""
        return Panel(
            index=self._index.copy(),
            close=self._close.copy(),
            quote_volume=self._quote_volume.copy(),
            stale=self._stale.copy(),
            symbols=self._symbols,
        )

    def randomize_after(self, t: datetime, seed: int) -> None:
        """Replace every column in every row strictly after `t` with seeded noise.

        Values are *replaced*, not scaled, so a NaN or a carried-forward price cannot
        survive the perturbation unchanged and make a leak look like a pass. Prices stay
        positive. The draw is a pure function of `seed`, so the checker is reproducible
        (CLAUDE.md rule 8).
        """
        start = int(np.searchsorted(self._index, _as_datetime64(t), side="right"))
        if start >= self.n_rows:
            return
        rng = np.random.default_rng(seed)
        shape = (self.n_rows - start, len(self._symbols))

        self._close[start:] = rng.uniform(*self._spread(self._close), size=shape)
        self._quote_volume[start:] = rng.uniform(*self._spread(self._quote_volume), size=shape)
        self._stale[start:] = rng.random(size=shape) < 0.5

    @staticmethod
    def _spread(array: np.ndarray) -> tuple[float, float]:
        """A positive (low, high) band covering the finite values of `array`."""
        finite = array[np.isfinite(array)]
        if finite.size == 0:
            return 1.0, 2.0
        low = float(np.min(finite))
        high = float(np.max(finite))
        if not low > 0:
            low = 1.0
            high = max(high, 2.0)
        if high <= low:
            high = low * 2.0
        return low, high


class PanelView:
    """A read-only window over rows `[0, stop)` of a panel.

    This is the only object strategy code ever receives. Every accessor is relative to
    `end_time`; none takes an absolute row index or a timestamp beyond it.

    Constructed by `Panel.slice_to`. Arrays come back as read-only views that share memory
    with the panel, so no copy is made and nothing can be written back through them.
    """

    __slots__ = ("_panel", "_stop")

    def __init__(self, panel: Panel, stop: int) -> None:
        self._panel = panel
        self._stop = stop

    # -- shape -------------------------------------------------------------------------

    @property
    def n_rows(self) -> int:
        return self._stop

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._panel._symbols

    @property
    def end_time(self) -> datetime:
        """Close time of the newest visible bar."""
        if self._stop == 0:
            raise ValueError("view is empty; no bar has closed yet")
        return _as_datetime(self._panel._index[self._stop - 1])

    # -- columns -----------------------------------------------------------------------

    def close(self, symbol: str) -> np.ndarray:
        """Visible close prices for `symbol`, oldest first. A read-only view."""
        return self._column(self._panel._close, symbol)

    def quote_volume(self, symbol: str) -> np.ndarray:
        """Visible quote-asset volume for `symbol`. Used directly as dollar volume."""
        return self._column(self._panel._quote_volume, symbol)

    def stale_mask(self, symbol: str) -> np.ndarray:
        """True where the bar was forward-filled rather than observed.

        The volatility estimator and the signal must skip these: a carried-forward price
        produces a zero return, which an estimator reads as calm (README section 3.2).
        """
        return self._column(self._panel._stale, symbol)

    def _column(self, array: np.ndarray, symbol: str) -> np.ndarray:
        view = array[: self._stop, self._symbol_index(symbol)]
        view.setflags(write=False)
        return view

    def _symbol_index(self, symbol: str) -> int:
        column = self._panel._column_of
        if symbol not in column:
            raise KeyError(f"{symbol!r} is not in this panel")
        return column[symbol]

    # -- point lookups -----------------------------------------------------------------

    def price_at_offset(self, symbol: str, hours_back: int) -> float:
        """Close price `hours_back` hours before `end_time`. `0` is the newest bar.

        Counting backwards from the end is the only addressing this view offers. A negative
        offset would name a bar that has not closed yet and is refused.
        """
        if hours_back < 0:
            raise ValueError(
                f"hours_back must not be negative; {hours_back} would name a future bar"
            )
        rows_back, remainder = divmod(
            hours_back * 3_600, int(self._panel._step.astype("timedelta64[s]").astype(int))
        )
        if remainder != 0:
            raise ValueError(f"{hours_back} hours is not a whole number of bars")
        row = self._stop - 1 - rows_back
        if row < 0:
            raise IndexError(
                f"{hours_back} hours back is before the start of the visible window "
                f"({self._stop} bars)"
            )
        return float(self._panel._close[row, self._symbol_index(symbol)])

    def last_prices(self) -> dict[str, float]:
        """Close price of the newest visible bar, per symbol, in sorted symbol order."""
        if self._stop == 0:
            raise ValueError("view is empty; no bar has closed yet")
        row = self._panel._close[self._stop - 1]
        return {symbol: float(row[i]) for i, symbol in enumerate(self._panel._symbols)}
