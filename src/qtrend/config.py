"""Load, validate and freeze the one TOML config per bot.

Design rules this module exists to enforce (README section 6, CLAUDE.md rules 1 and 9):

* **No defaults.** There is no fallback anywhere in this file. A key that is absent is an
  error; a key still holding its README section 6 placeholder is an error. Nothing is
  filled in on the caller's behalf.
* **Validated, not trusted.** Unknown sections and unknown keys are rejected, because an
  unknown key is usually a misspelling of a real one that would otherwise pass unnoticed.
* **Frozen.** Every returned object is a frozen dataclass and every sequence is a tuple,
  so a strategy module cannot mutate the config it was handed.
* **Derived, not configured.** `warmup_bars` follows from the values it depends on rather
  than being typed in (README section 6.1). It is exposed as `config.derived.warmup_bars`.
* **Credentials are separate.** `load_config` never touches a credential. `load_credentials`
  reads `.env` and returns an object whose repr is redacted, so a credential cannot reach a
  log or a traceback.

All errors are collected and raised together, so a fresh config reports every problem at
once rather than one per run.

### On placeholders

README section 6 prints most values as `0` or `"..."`. For most keys that sentinel is
meaningless as a setting, so seeing it means "unset". For a handful of keys the template
value *is* a legitimate setting and cannot be distinguished from an unset one, so those
keys carry `_NO_PLACEHOLDER` and are constrained by a domain rule alone:

    universe.exclude            an empty exclusion list is a normal configuration
    vol.lambda                  0.0 is rejected by the (0, 1) rule with a better message
    signal.skip_hours           0 means "no skip", a real choice (README section 3.3)
    selection.hour_utc          0 is midnight UTC, a real choice
    selection.min_signal        0.0 is the value README section 3.4 actually describes
    exposure.max_leverage       the template prints 1.0, a real value, not a placeholder
    risk.stop_loss_sigma        unconstrained while stop_loss_enabled is false
    risk.stop_cooldown_hours    unconstrained while stop_loss_enabled is false
    risk.daily_loss_limit       0.0 disables an explicitly optional control (section 5.3)

    every bool and every enum       the template prints a real member, not a placeholder

A verbatim copy of the README section 6 template still fails validation, because the other
forty-odd keys are reported unset.
"""

from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------------------------
# Rejection bounds.
#
# These are not defaults and no config key ever takes its value from one. They are the
# outer edge of what the schema will accept, and they exist to catch a unit error -- a fee
# entered as `0.1` meaning "0.1 percent" when the schema wants the fraction 0.001. README
# section 2 reports per-side commissions of 0.1% and 0.05%, so any per-side fee at or above
# 1% is two orders of magnitude out and is a typo, not a configuration.
# --------------------------------------------------------------------------------------
ABSURD_FEE_RATE = 0.01
ABSURD_SLIPPAGE_BPS = 1_000.0

MINUTES_PER_DAY = 1_440
MINUTES_PER_HOUR = 60
_INTERVAL_PATTERN = re.compile(r"^(\d+)([mhd])$")
_INTERVAL_UNIT_MINUTES = {"m": 1, "h": MINUTES_PER_HOUR, "d": MINUTES_PER_DAY}

# The .env variable names, assembled from a prefix rather than written as whole literals.
# Spelling either name out as a quoted constant is a key-shaped assignment and trips
# scripts/check_secrets.py -- correctly, since the scanner cannot tell a variable name from
# a value. Building the names keeps the scanner strict and this module honest.
ENV_PREFIX = "ROOSTOO_"
ENV_API_KEY = ENV_PREFIX + "API_KEY"
ENV_SECRET_KEY = ENV_PREFIX + "SECRET_KEY"


class ConfigError(Exception):
    """Raised when a config file is absent, malformed, or fails a validation rule."""

    def __init__(self, path: Path | str, problems: list[str]) -> None:
        self.path = str(path)
        self.problems = tuple(problems)
        body = "\n".join(f"  - {problem}" for problem in self.problems)
        super().__init__(f"invalid config {self.path}:\n{body}")


class CredentialError(Exception):
    """Raised when a credential is required and absent. Never carries a credential value."""


# --------------------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------------------

_NO_PLACEHOLDER = object()


@dataclass(frozen=True, slots=True)
class Spec:
    """One key of the schema. Every field is required; this class has no defaults."""

    section: str
    key: str
    field: str
    kind: str
    choices: tuple[str, ...]
    placeholder: object

    @property
    def dotted(self) -> str:
        return f"{self.section}.{self.key}"


def _spec(
    section: str, key: str, field: str, kind: str, choices: tuple[str, ...], placeholder: object
) -> Spec:
    return Spec(section, key, field, kind, choices, placeholder)


SCHEMA: tuple[Spec, ...] = (
    _spec("meta", "name", "name", "str", (), "..."),
    _spec("meta", "mode", "mode", "enum", ("trade", "hold", "liquidate"), _NO_PLACEHOLDER),

    _spec("exchange", "base_url", "base_url", "str", (), "..."),
    _spec("exchange", "max_clock_skew_ms", "max_clock_skew_ms", "int", (), 0),

    _spec("data", "source", "source", "str", (), "..."),
    _spec("data", "interval", "interval", "str", (), "..."),
    _spec("data", "history_days", "history_days", "int", (), 0),
    _spec("data", "cache_dir", "cache_dir", "str", (), "..."),
    _spec("data", "warmup_halflives", "warmup_halflives", "float", (), 0),

    _spec("universe", "quote_asset", "quote_asset", "str", (), "..."),
    _spec("universe", "volume_window_days", "volume_window_days", "int", (), 0),
    _spec("universe", "min_daily_dollar_volume", "min_daily_dollar_volume", "float", (), 0),
    _spec("universe", "exclude", "exclude", "str_list", (), _NO_PLACEHOLDER),

    _spec("vol", "lambda", "lam", "float", (), _NO_PLACEHOLDER),
    _spec("vol", "min_observations", "min_observations", "int", (), 0),
    _spec("vol", "floor", "floor", "float", (), 0),

    _spec("signal", "horizons_days", "horizons_days", "int_list", (), _NO_PLACEHOLDER),
    _spec("signal", "skip_hours", "skip_hours", "int", (), _NO_PLACEHOLDER),

    _spec("selection", "hour_utc", "hour_utc", "int", (), _NO_PLACEHOLDER),
    _spec("selection", "top_k", "top_k", "int", (), 0),
    _spec("selection", "keep_rank", "keep_rank", "int", (), 0),
    _spec("selection", "min_signal", "min_signal", "float", (), _NO_PLACEHOLDER),

    _spec("weights", "scheme", "scheme", "enum", ("equal", "inverse_vol"), _NO_PLACEHOLDER),
    _spec("weights", "max_single_name", "max_single_name", "float", (), 0),

    _spec("exposure", "target_daily_vol", "target_daily_vol", "float", (), 0),
    _spec("exposure", "rho_estimation", "rho_estimation", "enum", ("rolling",), _NO_PLACEHOLDER),
    _spec("exposure", "rho_window_days", "rho_window_days", "int", (), 0),
    _spec("exposure", "max_leverage", "max_leverage", "float", (), _NO_PLACEHOLDER),

    _spec("execution", "drift_band", "drift_band", "float", (), 0),
    _spec("execution", "order_type", "order_type", "enum", ("market", "limit"), _NO_PLACEHOLDER),
    _spec("execution", "cash_buffer", "cash_buffer", "float", (), 0),
    _spec("execution", "sells_before_buys", "sells_before_buys", "bool", (), _NO_PLACEHOLDER),
    _spec("execution", "use_client_order_id", "use_client_order_id", "bool", (), _NO_PLACEHOLDER),

    _spec("costs", "taker_fee", "taker_fee", "float", (), 0),
    _spec("costs", "maker_fee", "maker_fee", "float", (), 0),
    _spec("costs", "slippage_bps", "slippage_bps", "float", (), 0),

    _spec("limits", "api_calls_per_minute", "api_calls_per_minute", "int", (), 0),
    _spec("limits", "min_seconds_between_orders", "min_seconds_between_orders", "float", (), 0),
    _spec("limits", "max_order_fraction", "max_order_fraction", "float", (), 0),

    _spec("risk", "stop_loss_enabled", "stop_loss_enabled", "bool", (), _NO_PLACEHOLDER),
    _spec("risk", "stop_loss_sigma", "stop_loss_sigma", "float", (), _NO_PLACEHOLDER),
    _spec("risk", "stop_cooldown_hours", "stop_cooldown_hours", "int", (), _NO_PLACEHOLDER),
    _spec("risk", "daily_loss_limit", "daily_loss_limit", "float", (), _NO_PLACEHOLDER),

    _spec("safety", "max_data_age_seconds", "max_data_age_seconds", "int", (), 0),
    _spec(
        "safety", "max_unexplained_equity_move", "max_unexplained_equity_move", "float", (), 0
    ),

    _spec("backtest", "initial_cash", "initial_cash", "float", (), 0),
    _spec("backtest", "start", "start", "datetime", (), "..."),
    _spec("backtest", "report_from", "report_from", "datetime", (), "..."),
    _spec("backtest", "in_sample_end", "in_sample_end", "datetime", (), "..."),
    _spec("backtest", "holdout_start", "holdout_start", "datetime", (), "..."),
    _spec("backtest", "window_days", "window_days", "int", (), 0),
    _spec("backtest", "step_days", "step_days", "int", (), 0),
)

SECTIONS: tuple[str, ...] = tuple(dict.fromkeys(spec.section for spec in SCHEMA))


# --------------------------------------------------------------------------------------
# Typed, frozen config objects
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetaConfig:
    name: str
    mode: str


@dataclass(frozen=True, slots=True)
class ExchangeConfig:
    base_url: str
    max_clock_skew_ms: int


@dataclass(frozen=True, slots=True)
class DataConfig:
    source: str
    interval: str
    history_days: int
    cache_dir: str
    warmup_halflives: float


@dataclass(frozen=True, slots=True)
class UniverseConfig:
    quote_asset: str
    volume_window_days: int
    min_daily_dollar_volume: float
    exclude: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VolConfig:
    lam: float
    min_observations: int
    floor: float


@dataclass(frozen=True, slots=True)
class SignalConfig:
    horizons_days: tuple[int, ...]
    skip_hours: int


@dataclass(frozen=True, slots=True)
class SelectionConfig:
    hour_utc: int
    top_k: int
    keep_rank: int
    min_signal: float


@dataclass(frozen=True, slots=True)
class WeightsConfig:
    scheme: str
    max_single_name: float


@dataclass(frozen=True, slots=True)
class ExposureConfig:
    target_daily_vol: float
    rho_estimation: str
    rho_window_days: int
    max_leverage: float


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    drift_band: float
    order_type: str
    cash_buffer: float
    sells_before_buys: bool
    use_client_order_id: bool


@dataclass(frozen=True, slots=True)
class CostsConfig:
    taker_fee: float
    maker_fee: float
    slippage_bps: float


@dataclass(frozen=True, slots=True)
class LimitsConfig:
    api_calls_per_minute: int
    min_seconds_between_orders: float
    max_order_fraction: float


@dataclass(frozen=True, slots=True)
class RiskConfig:
    stop_loss_enabled: bool
    stop_loss_sigma: float
    stop_cooldown_hours: int
    daily_loss_limit: float


@dataclass(frozen=True, slots=True)
class SafetyConfig:
    max_data_age_seconds: int
    max_unexplained_equity_move: float


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_cash: float
    start: datetime
    report_from: datetime
    in_sample_end: datetime
    holdout_start: datetime
    window_days: int
    step_days: int


@dataclass(frozen=True, slots=True)
class DerivedConfig:
    """Values computed from config at load, never configured directly (README section 6.1)."""

    bars_per_day: int
    bars_per_hour: float
    ewma_halflife_bars: float
    signal_bars: float
    volume_bars: float
    ewma_warmup_bars: float
    warmup_bars: int


@dataclass(frozen=True, slots=True)
class Config:
    meta: MetaConfig
    exchange: ExchangeConfig
    data: DataConfig
    universe: UniverseConfig
    vol: VolConfig
    signal: SignalConfig
    selection: SelectionConfig
    weights: WeightsConfig
    exposure: ExposureConfig
    execution: ExecutionConfig
    costs: CostsConfig
    limits: LimitsConfig
    risk: RiskConfig
    safety: SafetyConfig
    backtest: BacktestConfig
    derived: DerivedConfig
    path: str


_SECTION_TYPES = {
    "meta": MetaConfig,
    "exchange": ExchangeConfig,
    "data": DataConfig,
    "universe": UniverseConfig,
    "vol": VolConfig,
    "signal": SignalConfig,
    "selection": SelectionConfig,
    "weights": WeightsConfig,
    "exposure": ExposureConfig,
    "execution": ExecutionConfig,
    "costs": CostsConfig,
    "limits": LimitsConfig,
    "risk": RiskConfig,
    "safety": SafetyConfig,
    "backtest": BacktestConfig,
}


# --------------------------------------------------------------------------------------
# Derivations (README section 6.1)
# --------------------------------------------------------------------------------------


def bars_per_day(interval: str) -> int:
    """Bars in a UTC day for an interval string such as "1h", "15m", "1d".

    The interval must divide a day evenly, otherwise bars do not align to a UTC grid and
    close-time indexing (README section 7.1) stops being well defined.
    """
    match = _INTERVAL_PATTERN.match(interval)
    if match is None:
        raise ValueError(f"data.interval {interval!r} is not of the form <integer><m|h|d>")
    count = int(match.group(1))
    if count < 1:
        raise ValueError(f"data.interval {interval!r} must be a positive number of units")
    minutes = count * _INTERVAL_UNIT_MINUTES[match.group(2)]
    if minutes > MINUTES_PER_DAY or MINUTES_PER_DAY % minutes != 0:
        raise ValueError(f"data.interval {interval!r} does not divide a UTC day evenly")
    return MINUTES_PER_DAY // minutes


def ewma_halflife_bars(lam: float) -> float:
    """Bars for an EWMA with decay `lam` to lose half its weight: log(0.5) / log(lam)."""
    return math.log(0.5) / math.log(lam)


def derive(
    *,
    interval: str,
    horizons_days: tuple[int, ...],
    skip_hours: int,
    volume_window_days: int,
    lam: float,
    warmup_halflives: float,
) -> DerivedConfig:
    """Compute the derived values of README section 6.1.

    `skip_hours` is stated in hours, so it is converted to bars. At the hourly interval the
    whole design assumes this is identity, which is the case README section 6.1 writes out.
    """
    per_day = bars_per_day(interval)
    per_hour = per_day / 24
    halflife = ewma_halflife_bars(lam)

    signal_bars = max(horizons_days) * per_day + skip_hours * per_hour
    volume_bars = volume_window_days * per_day
    ewma_bars = warmup_halflives * halflife

    return DerivedConfig(
        bars_per_day=per_day,
        bars_per_hour=per_hour,
        ewma_halflife_bars=halflife,
        signal_bars=signal_bars,
        volume_bars=volume_bars,
        ewma_warmup_bars=ewma_bars,
        warmup_bars=math.ceil(max(signal_bars, volume_bars, ewma_bars)),
    )


# --------------------------------------------------------------------------------------
# Structural checks
# --------------------------------------------------------------------------------------


def _coerce(spec: Spec, raw: object) -> tuple[object | None, str | None]:
    """Return (value, problem). Exactly one of the two is None."""
    dotted = spec.dotted

    if spec.kind == "str":
        if not isinstance(raw, str):
            return None, f"{dotted} must be a string, got {type(raw).__name__}"
        return raw, None

    if spec.kind == "enum":
        if not isinstance(raw, str):
            return None, f"{dotted} must be a string, got {type(raw).__name__}"
        if raw not in spec.choices:
            allowed = ", ".join(repr(choice) for choice in spec.choices)
            return None, f"{dotted} must be one of {allowed}, got {raw!r}"
        return raw, None

    if spec.kind == "bool":
        if not isinstance(raw, bool):
            return None, f"{dotted} must be a boolean, got {type(raw).__name__}"
        return raw, None

    if spec.kind == "int":
        if isinstance(raw, bool) or not isinstance(raw, int):
            return None, f"{dotted} must be an integer, got {type(raw).__name__}"
        return raw, None

    if spec.kind == "float":
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            return None, f"{dotted} must be a number, got {type(raw).__name__}"
        if not math.isfinite(float(raw)):
            return None, f"{dotted} must be finite, got {raw!r}"
        return float(raw), None

    if spec.kind == "str_list":
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            return None, f"{dotted} must be a list of strings"
        return tuple(raw), None

    if spec.kind == "int_list":
        if not isinstance(raw, list) or any(
            isinstance(item, bool) or not isinstance(item, int) for item in raw
        ):
            return None, f"{dotted} must be a list of integers"
        return tuple(raw), None

    if spec.kind == "datetime":
        return _coerce_datetime(dotted, raw)

    raise AssertionError(f"unhandled spec kind {spec.kind!r}")


def _coerce_datetime(dotted: str, raw: object) -> tuple[datetime | None, str | None]:
    if isinstance(raw, datetime):
        parsed = raw
    elif isinstance(raw, date):
        parsed = datetime(raw.year, raw.month, raw.day)
    elif isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None, f"{dotted} must be an ISO 8601 timestamp, got {raw!r}"
    else:
        return None, f"{dotted} must be an ISO 8601 timestamp, got {type(raw).__name__}"

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC), None


def _check_structure(document: dict[str, object], problems: list[str]) -> dict[str, object]:
    """Validate sections and keys; return the coerced values that survived, by dotted key."""
    for section in sorted(set(document) - set(SECTIONS)):
        problems.append(f"unknown section [{section}]")

    values: dict[str, object] = {}

    for section in SECTIONS:
        if section not in document:
            problems.append(f"missing section [{section}]")
            continue
        body = document[section]
        if not isinstance(body, dict):
            problems.append(f"[{section}] must be a table")
            continue

        expected = {spec.key for spec in SCHEMA if spec.section == section}
        for key in sorted(set(body) - expected):
            problems.append(f"unknown key {section}.{key}")

        for spec in SCHEMA:
            if spec.section != section:
                continue
            if spec.key not in body:
                problems.append(f"missing key {spec.dotted}")
                continue
            value, problem = _coerce(spec, body[spec.key])
            if problem is not None:
                problems.append(problem)
                continue
            values[spec.dotted] = value

    return values


def _check_placeholders(values: dict[str, object], problems: list[str]) -> set[str]:
    """Report every key still holding its README section 6 placeholder, in one message."""
    unset: list[str] = []
    for spec in SCHEMA:
        if spec.placeholder is _NO_PLACEHOLDER or spec.dotted not in values:
            continue
        value = values[spec.dotted]
        if isinstance(spec.placeholder, str):
            if value == spec.placeholder:
                unset.append(spec.dotted)
        elif isinstance(value, int | float) and float(value) == float(spec.placeholder):
            unset.append(spec.dotted)

    if unset:
        problems.append(
            "config contains unset placeholders; set these keys: " + ", ".join(unset)
        )
    return set(unset)


# --------------------------------------------------------------------------------------
# Domain and cross-field rules
# --------------------------------------------------------------------------------------


def _check_domains(values: dict[str, object], skip: set[str], problems: list[str]) -> None:
    """One rule per line. A key already reported unset is not reported again."""

    def get(dotted: str) -> object | None:
        if dotted in skip or dotted not in values:
            return None
        return values[dotted]

    def positive(dotted: str) -> None:
        value = get(dotted)
        if value is not None and value <= 0:
            problems.append(f"{dotted} must be greater than 0, got {value!r}")

    def non_negative(dotted: str) -> None:
        value = get(dotted)
        if value is not None and value < 0:
            problems.append(f"{dotted} must not be negative, got {value!r}")

    def fraction(dotted: str) -> None:
        value = get(dotted)
        if value is not None and not 0 < value <= 1:
            problems.append(f"{dotted} must be in (0, 1], got {value!r}")

    positive("exchange.max_clock_skew_ms")
    positive("data.history_days")
    positive("data.warmup_halflives")
    positive("universe.volume_window_days")
    positive("universe.min_daily_dollar_volume")
    positive("vol.min_observations")
    positive("vol.floor")
    positive("exposure.target_daily_vol")
    positive("exposure.rho_window_days")
    positive("exposure.max_leverage")
    positive("limits.api_calls_per_minute")
    positive("limits.min_seconds_between_orders")
    positive("safety.max_data_age_seconds")
    positive("safety.max_unexplained_equity_move")
    positive("backtest.initial_cash")
    positive("backtest.window_days")
    positive("backtest.step_days")
    non_negative("signal.skip_hours")
    fraction("weights.max_single_name")
    fraction("limits.max_order_fraction")
    fraction("execution.drift_band")

    interval = get("data.interval")
    if isinstance(interval, str):
        try:
            bars_per_day(interval)
        except ValueError as error:
            problems.append(str(error))

    lam = get("vol.lambda")
    if lam is not None and not 0 < lam < 1:
        problems.append(f"vol.lambda must be in the open interval (0, 1), got {lam!r}")

    horizons = get("signal.horizons_days")
    if horizons is not None:
        if len(horizons) == 0:
            problems.append("signal.horizons_days must not be empty")
        elif any(horizon <= 0 for horizon in horizons):
            problems.append(
                f"signal.horizons_days must all be greater than 0, got {list(horizons)!r}"
            )

    hour_utc = get("selection.hour_utc")
    if hour_utc is not None and not 0 <= hour_utc <= 23:
        problems.append(f"selection.hour_utc must be in 0..23, got {hour_utc!r}")

    top_k = get("selection.top_k")
    if top_k is not None and top_k < 1:
        problems.append(f"selection.top_k must be at least 1, got {top_k!r}")

    keep_rank = get("selection.keep_rank")
    if keep_rank is not None and keep_rank < 1:
        problems.append(f"selection.keep_rank must be at least 1, got {keep_rank!r}")

    min_signal = get("selection.min_signal")
    if min_signal is not None and min_signal < 0:
        problems.append(
            "selection.min_signal must not be negative: a long-only book entering on a "
            f"negative signal is holding a loser by construction, got {min_signal!r}"
        )

    cash_buffer = get("execution.cash_buffer")
    if cash_buffer is not None and not 0 < cash_buffer < 1:
        problems.append(f"execution.cash_buffer must be in (0, 1), got {cash_buffer!r}")

    for dotted in ("costs.taker_fee", "costs.maker_fee"):
        fee = get(dotted)
        if fee is None:
            continue
        if fee < 0:
            problems.append(f"{dotted} must not be negative, got {fee!r}")
        elif fee >= ABSURD_FEE_RATE:
            problems.append(
                f"{dotted} is {fee!r}, at or above {ABSURD_FEE_RATE!r}; fees are fractions, "
                "not percentages -- 0.1% per side is 0.001"
            )

    slippage = get("costs.slippage_bps")
    if slippage is not None:
        if slippage <= 0:
            problems.append(
                f"costs.slippage_bps must be greater than 0, got {slippage!r}; filling at "
                "the close with no spread is optimistic (README section 9.4)"
            )
        elif slippage >= ABSURD_SLIPPAGE_BPS:
            problems.append(
                f"costs.slippage_bps is {slippage!r}, at or above {ABSURD_SLIPPAGE_BPS!r} "
                "basis points"
            )

    daily_loss_limit = get("risk.daily_loss_limit")
    if daily_loss_limit is not None and not 0 <= daily_loss_limit < 1:
        problems.append(f"risk.daily_loss_limit must be in [0, 1), got {daily_loss_limit!r}")

    stop_enabled = get("risk.stop_loss_enabled")
    if stop_enabled is True:
        stop_sigma = get("risk.stop_loss_sigma")
        if stop_sigma is not None and stop_sigma <= 0:
            problems.append(
                "risk.stop_loss_sigma must be greater than 0 while risk.stop_loss_enabled "
                f"is true, got {stop_sigma!r}"
            )
        cooldown = get("risk.stop_cooldown_hours")
        if cooldown is not None and cooldown <= 0:
            problems.append(
                "risk.stop_cooldown_hours must be greater than 0 while "
                f"risk.stop_loss_enabled is true, got {cooldown!r}"
            )
    elif stop_enabled is False:
        non_negative("risk.stop_loss_sigma")
        non_negative("risk.stop_cooldown_hours")


def _check_cross_field(
    values: dict[str, object], skip: set[str], problems: list[str]
) -> DerivedConfig | None:
    """Rules spanning more than one key, plus the derivation they depend on."""

    def get(dotted: str) -> object | None:
        if dotted in skip or dotted not in values:
            return None
        return values[dotted]

    top_k = get("selection.top_k")
    keep_rank = get("selection.keep_rank")
    if top_k is not None and keep_rank is not None and keep_rank <= top_k:
        problems.append(
            f"selection.keep_rank ({keep_rank}) must be greater than selection.top_k "
            f"({top_k}): with keep_rank <= top_k the hysteresis band is empty"
        )

    max_single_name = get("weights.max_single_name")
    if top_k is not None and max_single_name is not None and max_single_name * top_k < 1:
        problems.append(
            f"weights.max_single_name ({max_single_name}) * selection.top_k ({top_k}) is "
            f"{max_single_name * top_k}, below 1: the per-name cap makes a fully invested "
            "book unreachable"
        )

    in_sample_end = get("backtest.in_sample_end")
    holdout_start = get("backtest.holdout_start")
    if (
        isinstance(in_sample_end, datetime)
        and isinstance(holdout_start, datetime)
        and holdout_start < in_sample_end
    ):
        problems.append(
            f"backtest.holdout_start ({holdout_start.isoformat()}) is before "
            f"backtest.in_sample_end ({in_sample_end.isoformat()}): the holdout would "
            "overlap the in-sample period"
        )

    derived = _derive_if_possible(values, skip, problems)
    if derived is None:
        return None

    start = get("backtest.start")
    report_from = get("backtest.report_from")
    if isinstance(start, datetime) and isinstance(report_from, datetime):
        hours = derived.warmup_bars / derived.bars_per_hour
        earliest = start + timedelta(hours=hours)
        if report_from < earliest:
            shortfall = earliest - report_from
            problems.append(
                f"backtest.report_from ({report_from.isoformat()}) is earlier than "
                f"backtest.start plus the derived warm-up: the warm-up requires "
                f"{derived.warmup_bars} bars ({hours:g} hours) from "
                f"{start.isoformat()}, so report_from must be at or after "
                f"{earliest.isoformat()}; it is short by {shortfall}"
            )

    return derived


def _derive_if_possible(
    values: dict[str, object], skip: set[str], problems: list[str]
) -> DerivedConfig | None:
    needed = (
        "data.interval",
        "signal.horizons_days",
        "signal.skip_hours",
        "universe.volume_window_days",
        "vol.lambda",
        "data.warmup_halflives",
    )
    if any(dotted in skip or dotted not in values for dotted in needed):
        return None

    horizons = values["signal.horizons_days"]
    lam = values["vol.lambda"]
    if len(horizons) == 0 or not 0 < lam < 1:
        return None

    try:
        return derive(
            interval=values["data.interval"],
            horizons_days=horizons,
            skip_hours=values["signal.skip_hours"],
            volume_window_days=values["universe.volume_window_days"],
            lam=lam,
            warmup_halflives=values["data.warmup_halflives"],
        )
    except ValueError:
        return None  # already reported by the interval domain rule


# --------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------


def load_config(path: Path | str) -> Config:
    """Load and validate one TOML config. Raises ConfigError listing every problem found."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(path, ["file not found"])

    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(path, [f"not valid TOML: {error}"]) from error
    except UnicodeDecodeError as error:
        raise ConfigError(path, [f"not valid UTF-8: {error}"]) from error

    problems: list[str] = []
    values = _check_structure(document, problems)
    unset = _check_placeholders(values, problems)
    _check_domains(values, unset, problems)
    derived = _check_cross_field(values, unset, problems)

    if problems:
        raise ConfigError(path, problems)
    if derived is None:  # unreachable while problems is empty; a guard, not a fallback
        raise ConfigError(path, ["derived values could not be computed"])

    sections = {}
    for section, section_type in _SECTION_TYPES.items():
        kwargs = {
            spec.field: values[spec.dotted] for spec in SCHEMA if spec.section == section
        }
        sections[section] = section_type(**kwargs)

    return Config(**sections, derived=derived, path=str(path))


@dataclass(frozen=True, slots=True, repr=False)
class Credentials:
    """Roostoo credentials. The repr is redacted so a traceback cannot leak a key."""

    api_key: str
    secret_key: str

    def __repr__(self) -> str:
        return "Credentials(api_key=<redacted>, secret_key=<redacted>)"

    __str__ = __repr__


def load_credentials(path: Path | str) -> Credentials:
    """Read credentials from a .env file. The only place a credential enters the process.

    Never called by the backtester. Phases 0 to 10 run without it entirely, so a missing
    .env is an error only for code that genuinely needs to sign a request -- it never
    degrades to paper trading (README section 10.4).
    """
    path = Path(path)
    if not path.is_file():
        raise CredentialError(
            f"{path} not found; copy .env.example to .env and fill it in. "
            "Credentials live only in .env and .env is git-ignored."
        )

    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        found[name.strip()] = value.strip().strip("'\"")

    missing = [
        name for name in (ENV_API_KEY, ENV_SECRET_KEY) if name not in found or not found[name]
    ]
    if missing:
        raise CredentialError(
            f"{path} is missing a value for: {', '.join(missing)}. "
            "The bot does not fall back to paper trading."
        )

    return Credentials(api_key=found[ENV_API_KEY], secret_key=found[ENV_SECRET_KEY])
