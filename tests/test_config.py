"""Tests for qtrend.config.

Every fixture builds its config explicitly, key by key (README section 6: "no hidden
constants in tests ... so a schema change breaks the tests loudly"). The values below are
test data, not a configuration: nothing here is a suggested setting and no file under
configs/ is written by this module.
"""

from __future__ import annotations

import ast
import math
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qtrend.config import (
    ABSURD_FEE_RATE,
    ENV_API_KEY,
    ENV_SECRET_KEY,
    SCHEMA,
    ConfigError,
    CredentialError,
    bars_per_day,
    ewma_halflife_bars,
    load_config,
    load_credentials,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_SOURCE = REPO_ROOT / "src" / "qtrend" / "config.py"


# --------------------------------------------------------------------------------------
# Fixture: one complete, internally consistent config, declared key by key
# --------------------------------------------------------------------------------------


def valid_document() -> dict[str, dict[str, object]]:
    """A config that passes every rule. Test data -- not a recommended configuration.

    The warm-up implied by these values is
        max(30 days * 24 bars + 6 hours, 30 days * 24 bars, 5 * halflife(0.94))
        = 726 bars = 726 hours,
    so report_from sits comfortably beyond start + 726h.
    """
    return {
        "meta": {"name": "test", "mode": "hold"},
        "exchange": {"base_url": "https://exchange.invalid", "max_clock_skew_ms": 5_000},
        "data": {
            "source": "test-source",
            "interval": "1h",
            "history_days": 400,
            "cache_dir": "data/cache",
            "warmup_halflives": 5.0,
        },
        "universe": {
            "quote_asset": "USD",
            "volume_window_days": 30,
            "min_daily_dollar_volume": 10_000_000.0,
            "exclude": [],
        },
        "vol": {"lambda": 0.94, "min_observations": 100, "floor": 0.005},
        "signal": {"horizons_days": [7, 30], "skip_hours": 6},
        "selection": {"hour_utc": 0, "top_k": 5, "keep_rank": 8, "min_signal": 0.0},
        "weights": {"scheme": "equal", "max_single_name": 0.3},
        "exposure": {
            "target_daily_vol": 0.02,
            "rho_estimation": "rolling",
            "rho_window_days": 60,
            "max_leverage": 1.0,
        },
        "execution": {
            "drift_band": 0.2,
            "order_type": "market",
            "cash_buffer": 0.02,
            "sells_before_buys": True,
            "use_client_order_id": True,
        },
        "costs": {"taker_fee": 0.001, "maker_fee": 0.0005, "slippage_bps": 5.0},
        "limits": {
            "api_calls_per_minute": 60,
            "min_seconds_between_orders": 60.0,
            "max_order_fraction": 0.25,
        },
        "risk": {
            "stop_loss_enabled": False,
            "stop_loss_sigma": 0.0,
            "stop_cooldown_hours": 0,
            "daily_loss_limit": 0.0,
        },
        "safety": {"max_data_age_seconds": 7_200, "max_unexplained_equity_move": 0.1},
        "backtest": {
            "initial_cash": 100_000.0,
            "start": "2024-01-01T00:00:00Z",
            "report_from": "2024-02-01T00:00:00Z",
            "in_sample_end": "2025-01-01T00:00:00Z",
            "holdout_start": "2025-01-02T00:00:00Z",
            "window_days": 14,
            "step_days": 1,
        },
    }


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, int):
        return str(value)
    raise AssertionError(f"test helper cannot serialise {value!r}")


def to_toml(document: dict[str, dict[str, object]]) -> str:
    lines: list[str] = []
    for section, body in document.items():
        lines.append(f"[{section}]")
        for key, value in body.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


def write_config(tmp_path: Path, document: dict[str, dict[str, object]]) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(to_toml(document), encoding="utf-8")
    return path


def load_with(tmp_path: Path, **overrides: dict[str, object]):
    """Load the valid document with the named section keys replaced."""
    document = valid_document()
    for section, changes in overrides.items():
        document[section].update(changes)
    return load_config(write_config(tmp_path, document))


def problems_from(excinfo: pytest.ExceptionInfo[ConfigError]) -> tuple[str, ...]:
    return excinfo.value.problems


def assert_problem(excinfo: pytest.ExceptionInfo[ConfigError], fragment: str) -> None:
    problems = problems_from(excinfo)
    assert any(fragment in problem for problem in problems), (
        f"expected a problem containing {fragment!r}, got {problems!r}"
    )


# --------------------------------------------------------------------------------------
# The fixture tracks the schema
# --------------------------------------------------------------------------------------


def test_fixture_declares_exactly_the_schema():
    """A key added to SCHEMA without a fixture value breaks here, loudly."""
    document = valid_document()
    declared = {(section, key) for section, body in document.items() for key in body}
    expected = {(spec.section, spec.key) for spec in SCHEMA}
    assert declared == expected, (
        f"fixture missing {sorted(expected - declared)}, "
        f"fixture has extra {sorted(declared - expected)}"
    )


def test_valid_config_loads(tmp_path):
    config = load_with(tmp_path)
    assert config.meta.mode == "hold"
    assert config.vol.lam == 0.94
    assert config.signal.horizons_days == (7, 30)
    assert config.backtest.start == datetime(2024, 1, 1, tzinfo=UTC)


def test_config_objects_are_frozen(tmp_path):
    config = load_with(tmp_path)
    with pytest.raises((AttributeError, TypeError)):
        config.selection.top_k = 99


def test_sequences_are_tuples_not_lists(tmp_path):
    config = load_with(tmp_path)
    assert isinstance(config.signal.horizons_days, tuple)
    assert isinstance(config.universe.exclude, tuple)


# --------------------------------------------------------------------------------------
# Placeholders
# --------------------------------------------------------------------------------------


def readme_template() -> dict[str, dict[str, object]]:
    """The config block printed in README section 6, parsed out of README.md itself."""
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```toml\n(.*?)```", text, flags=re.DOTALL)
    assert len(blocks) == 1, f"expected one toml block in README.md, found {len(blocks)}"
    return tomllib.loads(blocks[0])


def test_readme_template_is_rejected_and_names_the_unset_keys(tmp_path):
    """The template in README section 6 must never load. This is the point of section 6."""
    path = tmp_path / "template.toml"
    path.write_text(to_toml(readme_template()), encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    assert_problem(excinfo, "config contains unset placeholders; set these keys:")
    message = str(excinfo.value)
    for dotted in (
        "meta.name",
        "exchange.base_url",
        "data.source",
        "data.interval",
        "universe.min_daily_dollar_volume",
        "selection.top_k",
        "costs.taker_fee",
        "backtest.initial_cash",
        "backtest.start",
        "backtest.report_from",
    ):
        assert dotted in message, f"{dotted} not named among the unset keys"


def test_readme_template_covers_the_whole_schema():
    """README section 6 and SCHEMA must agree on which keys exist."""
    template = readme_template()
    declared = {(section, key) for section, body in template.items() for key in body}
    expected = {(spec.section, spec.key) for spec in SCHEMA}
    assert declared == expected, (
        f"README has keys the schema lacks: {sorted(declared - expected)}; "
        f"schema has keys README lacks: {sorted(expected - declared)}"
    )


def test_one_placeholder_left_behind_is_caught(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_with(tmp_path, costs={"taker_fee": 0.0})
    assert_problem(excinfo, "config contains unset placeholders; set these keys: costs.taker_fee")


def test_legitimate_zeros_are_not_treated_as_placeholders(tmp_path):
    """hour_utc=0, skip_hours=0 and min_signal=0.0 are real settings, not unset keys."""
    config = load_with(
        tmp_path,
        selection={"hour_utc": 0, "min_signal": 0.0},
        signal={"skip_hours": 0},
        universe={"exclude": []},
    )
    assert config.selection.hour_utc == 0
    assert config.signal.skip_hours == 0
    assert config.selection.min_signal == 0.0
    assert config.universe.exclude == ()


# --------------------------------------------------------------------------------------
# One test per validation rule named in CLAUDE.md Phase 1
# --------------------------------------------------------------------------------------


def test_keep_rank_must_exceed_top_k(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_with(tmp_path, selection={"top_k": 5, "keep_rank": 5})
    assert_problem(
        excinfo,
        "selection.keep_rank (5) must be greater than selection.top_k (5): "
        "with keep_rank <= top_k the hysteresis band is empty",
    )


def test_keep_rank_below_top_k_is_rejected(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_with(tmp_path, selection={"top_k": 5, "keep_rank": 3})
    assert_problem(excinfo, "must be greater than selection.top_k (5)")


def test_max_single_name_times_top_k_below_one(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_with(
            tmp_path,
            selection={"top_k": 4, "keep_rank": 6},
            weights={"max_single_name": 0.2},
        )
    assert_problem(
        excinfo,
        "weights.max_single_name (0.2) * selection.top_k (4) is 0.8, below 1: "
        "the per-name cap makes a fully invested book unreachable",
    )


@pytest.mark.parametrize("lam", [0.0, 1.0, 1.5, -0.5])
def test_lambda_outside_the_open_unit_interval(tmp_path, lam):
    with pytest.raises(ConfigError) as excinfo:
        load_with(tmp_path, vol={"lambda": lam})
    assert_problem(excinfo, f"vol.lambda must be in the open interval (0, 1), got {lam!r}")


def test_horizons_days_must_not_be_empty(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_with(tmp_path, signal={"horizons_days": []})
    assert_problem(excinfo, "signal.horizons_days must not be empty")


def test_negative_fee_is_rejected(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_with(tmp_path, costs={"taker_fee": -0.001})
    assert_problem(excinfo, "costs.taker_fee must not be negative, got -0.001")


def test_absurd_fee_is_rejected(tmp_path):
    """A fee entered as a percentage rather than a fraction is the error being caught."""
    with pytest.raises(ConfigError) as excinfo:
        load_with(tmp_path, costs={"taker_fee": 0.1})
    assert_problem(
        excinfo,
        f"costs.taker_fee is 0.1, at or above {ABSURD_FEE_RATE!r}; fees are fractions, "
        "not percentages -- 0.1% per side is 0.001",
    )


def test_report_from_earlier_than_start_plus_warmup(tmp_path):
    """The error must state the required warm-up and the shortfall (README section 6.1)."""
    with pytest.raises(ConfigError) as excinfo:
        load_with(tmp_path, backtest={"report_from": "2024-01-10T00:00:00Z"})

    assert_problem(excinfo, "backtest.report_from (2024-01-10T00:00:00+00:00) is earlier than")
    assert_problem(excinfo, "the warm-up requires 726 bars (726 hours)")
    assert_problem(excinfo, "report_from must be at or after 2024-01-31T06:00:00+00:00")
    assert_problem(excinfo, "it is short by 21 days, 6:00:00")


def test_report_from_exactly_at_the_warmup_boundary_is_accepted(tmp_path):
    config = load_with(tmp_path, backtest={"report_from": "2024-01-31T06:00:00Z"})
    assert config.derived.warmup_bars == 726


def test_holdout_start_before_in_sample_end(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_with(
            tmp_path,
            backtest={
                "in_sample_end": "2025-01-01T00:00:00Z",
                "holdout_start": "2024-12-01T00:00:00Z",
            },
        )
    assert_problem(
        excinfo,
        "backtest.holdout_start (2024-12-01T00:00:00+00:00) is before "
        "backtest.in_sample_end (2025-01-01T00:00:00+00:00): the holdout would overlap "
        "the in-sample period",
    )


# --------------------------------------------------------------------------------------
# Structural validation
# --------------------------------------------------------------------------------------


def test_missing_key_is_named(tmp_path):
    document = valid_document()
    del document["selection"]["top_k"]
    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(tmp_path, document))
    assert_problem(excinfo, "missing key selection.top_k")


def test_missing_section_is_named(tmp_path):
    document = valid_document()
    del document["risk"]
    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(tmp_path, document))
    assert_problem(excinfo, "missing section [risk]")


def test_unknown_key_is_rejected(tmp_path):
    """An unknown key is usually a misspelling that would otherwise pass unnoticed."""
    with pytest.raises(ConfigError) as excinfo:
        load_with(tmp_path, selection={"top_kk": 5})
    assert_problem(excinfo, "unknown key selection.top_kk")


def test_unknown_section_is_rejected(tmp_path):
    document = valid_document()
    document["tuning"] = {"magic": 1}
    with pytest.raises(ConfigError) as excinfo:
        load_config(write_config(tmp_path, document))
    assert_problem(excinfo, "unknown section [tuning]")


def test_wrong_type_is_rejected(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_with(tmp_path, selection={"top_k": "five"})
    assert_problem(excinfo, "selection.top_k must be an integer, got str")


def test_enum_member_is_enforced(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_with(tmp_path, meta={"mode": "yolo"})
    assert_problem(excinfo, "meta.mode must be one of 'trade', 'hold', 'liquidate', got 'yolo'")


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path / "absent.toml")
    assert_problem(excinfo, "file not found")


def test_malformed_toml_is_reported(tmp_path):
    path = tmp_path / "broken.toml"
    path.write_text("[meta\nname = ", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert_problem(excinfo, "not valid TOML")


def test_every_problem_is_reported_at_once(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_with(
            tmp_path,
            vol={"lambda": 2.0},
            signal={"horizons_days": []},
            selection={"top_k": 5, "keep_rank": 2},
        )
    problems = problems_from(excinfo)
    assert len(problems) >= 3, problems


# --------------------------------------------------------------------------------------
# Derived values (README section 6.1)
# --------------------------------------------------------------------------------------


def test_bars_per_day_from_interval():
    assert bars_per_day("1h") == 24
    assert bars_per_day("15m") == 96
    assert bars_per_day("4h") == 6
    assert bars_per_day("1d") == 1


def test_interval_that_does_not_divide_a_day_is_rejected():
    with pytest.raises(ValueError, match="does not divide a UTC day evenly"):
        bars_per_day("7h")


def test_config_rejects_non_hourly_intervals_until_strategy_supports_them(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_with(tmp_path, data={"interval": "15m"})
    assert_problem(excinfo, "currently require one-hour bars")


def test_ewma_halflife_matches_its_definition():
    """At the half-life the EWMA weight has decayed to one half."""
    lam = 0.94
    halflife = ewma_halflife_bars(lam)
    assert math.isclose(lam**halflife, 0.5)


def test_warmup_bars_matches_the_readme_formula(tmp_path):
    config = load_with(tmp_path)
    expected = max(
        max(config.signal.horizons_days) * 24 + config.signal.skip_hours,
        config.universe.volume_window_days * 24,
        config.data.warmup_halflives * ewma_halflife_bars(config.vol.lam),
    )
    assert config.derived.warmup_bars == math.ceil(expected)


def test_warmup_bars_changes_when_lambda_changes(tmp_path):
    """A higher lambda means a longer half-life means a longer warm-up."""
    low = load_with(
        tmp_path,
        vol={"lambda": 0.94},
        data={"warmup_halflives": 200.0},
        backtest={"report_from": "2026-01-01T00:00:00Z"},
    )
    high = load_with(
        tmp_path,
        vol={"lambda": 0.99},
        data={"warmup_halflives": 200.0},
        backtest={"report_from": "2026-01-01T00:00:00Z"},
    )
    assert high.derived.warmup_bars > low.derived.warmup_bars
    assert high.derived.ewma_halflife_bars > low.derived.ewma_halflife_bars


def test_warmup_bars_changes_when_horizons_days_changes(tmp_path):
    short = load_with(tmp_path, signal={"horizons_days": [7, 30]})
    long = load_with(
        tmp_path,
        signal={"horizons_days": [7, 90]},
        backtest={"report_from": "2024-06-01T00:00:00Z"},
    )
    assert long.derived.warmup_bars > short.derived.warmup_bars
    assert long.derived.warmup_bars == 90 * 24 + 6


def test_warmup_bars_changes_when_volume_window_days_changes(tmp_path):
    short = load_with(tmp_path, universe={"volume_window_days": 30})
    long = load_with(
        tmp_path,
        universe={"volume_window_days": 120},
        backtest={"report_from": "2024-06-01T00:00:00Z"},
    )
    assert long.derived.warmup_bars > short.derived.warmup_bars
    assert long.derived.warmup_bars == 120 * 24


def test_warmup_bars_is_the_max_of_the_three_terms(tmp_path):
    config = load_with(tmp_path)
    assert config.derived.warmup_bars == math.ceil(
        max(
            config.derived.signal_bars,
            config.derived.volume_bars,
            config.derived.ewma_warmup_bars,
        )
    )


def test_warmup_bars_is_not_a_config_key():
    """README section 6.1: warmup_bars is derived, never configured."""
    assert all(spec.key != "warmup_bars" for spec in SCHEMA)
    assert "warmup_bars" not in valid_document()["data"]


# --------------------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------------------


def test_missing_env_file_raises(tmp_path):
    with pytest.raises(CredentialError, match="copy .env.example to .env"):
        load_credentials(tmp_path / ".env")


def test_blank_credentials_are_named(tmp_path):
    path = tmp_path / ".env"
    path.write_text(f"{ENV_API_KEY}=\n{ENV_SECRET_KEY}=\n", encoding="utf-8")
    with pytest.raises(CredentialError) as excinfo:
        load_credentials(path)
    assert ENV_API_KEY in str(excinfo.value)
    assert ENV_SECRET_KEY in str(excinfo.value)
    assert "does not fall back to paper trading" in str(excinfo.value)


def test_credentials_repr_is_redacted(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        f"{ENV_API_KEY}=NOT-A-REAL-KEY-0001\n{ENV_SECRET_KEY}=NOT-A-REAL-KEY-0002\n",
        encoding="utf-8",
    )
    credentials = load_credentials(path)

    assert credentials.api_key == "NOT-A-REAL-KEY-0001"
    for rendered in (repr(credentials), str(credentials), f"{credentials}"):
        assert "NOT-A-REAL-KEY-0001" not in rendered
        assert "NOT-A-REAL-KEY-0002" not in rendered
        assert "<redacted>" in rendered


def test_load_config_never_reads_credentials(tmp_path):
    """Phases 0 to 10 run with no credentials at all."""
    config = load_with(tmp_path)
    assert not hasattr(config, "credentials")
    assert "api_key" not in str(config)


# --------------------------------------------------------------------------------------
# The Phase 1 gate: no default value anywhere in config.py
# --------------------------------------------------------------------------------------


def test_no_dataclass_field_has_a_default():
    tree = ast.parse(CONFIG_SOURCE.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign) and statement.value is not None:
                offenders.append(f"{node.name}.{ast.unparse(statement.target)}")
    assert offenders == [], f"dataclass fields with defaults: {offenders}"


def test_no_fallback_lookups_in_config_source():
    """No dict.get(key, fallback), no getattr(obj, name, fallback), no setdefault."""
    tree = ast.parse(CONFIG_SOURCE.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_lookup = isinstance(func, ast.Attribute) and func.attr in {"get", "setdefault"}
        if is_lookup and (func.attr == "setdefault" or len(node.args) > 1):
            offenders.append(ast.unparse(node))
        if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) > 2:
            offenders.append(ast.unparse(node))
    assert offenders == [], f"fallback lookups in config.py: {offenders}"


def test_no_function_parameter_supplies_a_config_default():
    """Keyword defaults would let a caller omit a value and get an invented one."""
    tree = ast.parse(CONFIG_SOURCE.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        args = node.args
        if args.defaults or [default for default in args.kw_defaults if default is not None]:
            offenders.append(node.name)
    assert offenders == [], f"functions with parameter defaults: {offenders}"
