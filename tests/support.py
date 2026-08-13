"""Fixtures shared by the tests."""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

from veritas.compute import DIMENSIONS, MEASURES
from veritas.facts import Fact, FactBook, Provenance, ShortlistItem

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "config" / "registry.toml"
BRIEF_PATH = REPO_ROOT / "config" / "audience_brief.md"

WEEK_END = "2026-08-08"
PRIOR_WEEK_END = "2026-08-01"
WEEK_START = "2026-08-02"
AS_OF = date(2026, 8, 13)


def make_fact(
    fact_id: str,
    value: float | None,
    unit: str = "usd",
    precision: int = 0,
    display: str | None = None,
    flags: tuple[str, ...] = (),
    period_start: str = WEEK_START,
) -> Fact:
    metric_id, cut, period, statistic = fact_id.split("/")
    period_type, period_end = period.split(":")
    return Fact(
        id=fact_id,
        metric_id=metric_id,
        label=f"{metric_id} | {cut} | {period} | {statistic}",
        statistic_key=statistic,
        cut_dim=None if cut == "overall" else cut.split("=")[0],
        cut_value=None if cut == "overall" else cut.split("=")[1],
        period_type=period_type,
        period_start=period_start,
        period_end=period_end,
        value=value,
        unit=unit,
        display_precision=precision,
        display=display if display is not None else str(value),
        provenance=Provenance("aggregate", "SUM(gmv) over the period"),
        flags=flags,
    )


def tiny_factbook() -> FactBook:
    """A two-fact book with one shortlist entry, for validator tests."""
    book = FactBook(
        run_id="2026-08-13-testrun01",
        as_of="2026-08-13",
        generated_at="2026-08-13T00:00:00+00:00",
        week_start=WEEK_START,
        week_end=WEEK_END,
        partial_week=None,
    )
    packed = [
        make_fact(f"gmv/overall/complete_week:{WEEK_END}/value", 1_234_567.0, display="$1,234,567"),
        make_fact(
            f"gmv/overall/complete_week:{PRIOR_WEEK_END}/value",
            1_346_890.0,
            display="$1,346,890",
            period_start="2026-07-26",
        ),
        make_fact(f"gmv/channel=email/complete_week:{WEEK_END}/value", 0.0, display="$0"),
        make_fact(
            f"conversion_rate/overall/complete_week:{WEEK_END}/wow_pct",
            2.5,
            unit="percent",
            precision=1,
            display="2.5%",
        ),
        make_fact(
            f"gmv/overall/complete_week:{WEEK_END}/wow_pct",
            -8.34,
            unit="percent",
            precision=1,
            display="-8.3%",
        ),
        make_fact(
            f"conversion_rate/overall/complete_week:{WEEK_END}/value",
            None,
            unit="percent",
            precision=2,
            display="n/a",
            flags=("no_exposure",),
        ),
    ]
    unpacked = make_fact(
        f"gmv/country=US/complete_week:{WEEK_END}/value", 500_000.0, display="$500,000"
    )
    for fact in [*packed, unpacked]:
        book.add(fact)

    book.shortlist = [
        ShortlistItem(
            metric_id="gmv",
            cut_dim=None,
            cut_value=None,
            channels=("overall",),
            rank=1,
            fact_ids=tuple(f.id for f in packed),
        )
    ]
    book.coverage = [
        {"metric_id": "gmv", "status": "ok", "required": True, "detail": ""},
    ]
    return book


def highlight(
    narrative: str = "GMV for overall came in at $1,234,567 in the week ending 2026-08-08.",
    claims: list[dict] | None = None,
    title: str = "GMV moved",
    hypothesis: str | None = None,
    metric_id: str = "gmv",
    severity: str = "high",
) -> dict:
    return {
        "title": title,
        "metric_id": metric_id,
        "cut": "overall",
        "severity": severity,
        "narrative": narrative,
        "hypothesis": hypothesis,
        "claims": claims
        if claims is not None
        else [{"fact_id": f"gmv/overall/complete_week:{WEEK_END}/value", "value": 1_234_567.0}],
    }


def response(highlights: list[dict], dismissals: list[dict] | None = None) -> str:
    import json

    return json.dumps({"highlights": highlights, "dismissals": dismissals or []})


def write_source(
    path: Path,
    start: date,
    end: date,
    sessions: int = 1000,
    orders: int = 30,
    gmv: float = 2400.0,
    countries: tuple[str, ...] = ("US",),
    channels: tuple[str, ...] = ("organic",),
) -> Path:
    """A flat, well-formed source file covering ``start``..``end`` inclusive."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["date", *DIMENSIONS, *MEASURES])
        day = start
        while day <= end:
            for country in countries:
                for channel in channels:
                    writer.writerow([day.isoformat(), country, channel, sessions, orders, f"{gmv:.2f}"])
            day += timedelta(days=1)
    return path
