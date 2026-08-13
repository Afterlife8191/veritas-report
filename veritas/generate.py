"""Seeded generator for the synthetic e-commerce dataset.

Emits one row per ``(date, country, channel)`` with sessions, orders and GMV for
18 months. The series carry a growth trend, weekday and annual seasonality, and
multiplicative noise -- plus a set of **planted events** the pipeline is meant to
find. The events are listed in :data:`PLANTED_EVENTS` and in the README; nothing
downstream knows about them, so they double as an end-to-end check on the stats
layer.

Everything is driven by ``random.Random(seed)``: same seed, same file, byte for
byte, on any machine.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from random import Random

# The dataset is anchored to fixed dates so the demo is reproducible: the report
# says the same thing next year as it does today.
START_DATE = date(2025, 2, 9)  # a Sunday
END_DATE = date(2026, 8, 12)  # a Wednesday -> the last week is partial
DEFAULT_SEED = 20260813

COUNTRIES = {"US": 1.00, "UK": 0.55, "DE": 0.50, "JP": 0.40, "BR": 0.30}
CHANNELS = {
    "organic": 0.35,
    "paid_search": 0.28,
    "social": 0.15,
    "email": 0.12,
    "affiliate": 0.10,
    "display_ads": 0.06,  # switched off mid-dataset; see PLANTED_EVENTS
    "marketplace": 0.00,  # launches mid-dataset; see PLANTED_EVENTS
}

BASE_DAILY_SESSIONS = 42_000.0
DAILY_GROWTH = 0.00045  # ~ +28% across the 18 months

WEEKDAY_FACTOR = {  # date.weekday(): Mon=0 .. Sun=6
    0: 1.06, 1: 1.05, 2: 1.03, 3: 1.02, 4: 0.99, 5: 0.93, 6: 0.92,
}

BASE_CVR = {
    "organic": 0.030,
    "paid_search": 0.024,
    "social": 0.016,
    "email": 0.052,
    "affiliate": 0.028,
    "display_ads": 0.012,
    "marketplace": 0.034,
}
COUNTRY_CVR_FACTOR = {"US": 1.00, "UK": 0.95, "DE": 0.90, "JP": 1.10, "BR": 0.75}
BASE_AOV = {"US": 82.0, "UK": 76.0, "DE": 71.0, "JP": 95.0, "BR": 48.0}
CHANNEL_AOV_FACTOR = {
    "organic": 1.00,
    "paid_search": 0.96,
    "social": 0.88,
    "email": 1.15,
    "affiliate": 0.90,
    "display_ads": 0.82,
    "marketplace": 1.04,
}

SESSION_NOISE_SIGMA = 0.06
CVR_NOISE_SIGMA = 0.05
AOV_NOISE_SIGMA = 0.04

#: A market-wide multiplier shared by every cell on a given day. Without it, the
#: only day-to-day variation is per-cell noise, which averages away in any
#: aggregate and leaves totals implausibly smooth -- so ordinary days would score
#: as anomalies and the anomaly detector would look far better than it is.
DAY_SHOCK_SIGMA = 0.055

# Planted signal. Each entry names what a correct pipeline should surface, and at
# which cut dimension -- an event buried inside a country x channel cell that no
# registered cut exposes would be undetectable by construction, which would make
# it a bad test rather than a hard one.
PLANTED_EVENTS = [
    "DE conversion steps down ~35% on paid_search from 2026-08-02 (country + channel cuts)",
    "JP demand spikes ~2.3x on 2026-08-05 only (single-day anomaly, country cut)",
    "affiliate order value collapses ~45% in the US during the week under review",
    "display_ads is switched off entirely from 2026-08-02 (disappeared cut)",
    "marketplace launches 2026-08-02 in US and UK (new cut, no baseline at all)",
    "email sessions ramp ~40% over the final six weeks (sustained trend, not a spike)",
]

WEEK_UNDER_REVIEW_START = date(2026, 8, 2)

DE_CVR_STEP_FROM = WEEK_UNDER_REVIEW_START
JP_SPIKE_DAY = date(2026, 8, 5)
AFFILIATE_SHOCK = (WEEK_UNDER_REVIEW_START, date(2026, 8, 8))
DISPLAY_ADS_STOPS = WEEK_UNDER_REVIEW_START
MARKETPLACE_LAUNCH = WEEK_UNDER_REVIEW_START
EMAIL_RAMP_FROM = date(2026, 7, 1)

FIELDNAMES = ["date", "country", "channel", "sessions", "orders", "gmv"]


@dataclass(frozen=True)
class Row:
    day: date
    country: str
    channel: str
    sessions: int
    orders: int
    gmv: float


def _annual_factor(day: date) -> float:
    return 1.0 + 0.08 * math.sin(2 * math.pi * (day.timetuple().tm_yday - 30) / 365.0)


def _session_scale(day: date, country: str, channel: str, day_index: int) -> float:
    """Multiplicative session drivers for one cell, before noise."""
    scale = (
        BASE_DAILY_SESSIONS
        * COUNTRIES[country]
        * CHANNELS[channel]
        * (1.0 + DAILY_GROWTH * day_index)
        * WEEKDAY_FACTOR[day.weekday()]
        * _annual_factor(day)
    )
    if channel == "marketplace":
        if day < MARKETPLACE_LAUNCH or country not in ("US", "UK"):
            return 0.0
        ramp_days = (day - MARKETPLACE_LAUNCH).days
        scale = (
            BASE_DAILY_SESSIONS
            * COUNTRIES[country]
            * 0.05
            * min(1.0, 0.4 + 0.1 * ramp_days)
            * WEEKDAY_FACTOR[day.weekday()]
        )
    if channel == "display_ads" and day >= DISPLAY_ADS_STOPS:
        return 0.0
    if channel == "email" and day >= EMAIL_RAMP_FROM:
        ramp = min(1.0, (day - EMAIL_RAMP_FROM).days / 42.0)
        scale *= 1.0 + 0.40 * ramp
    if country == "JP" and day == JP_SPIKE_DAY:
        scale *= 2.3
    if country == "US" and channel == "affiliate" and AFFILIATE_SHOCK[0] <= day <= AFFILIATE_SHOCK[1]:
        scale *= 0.92
    return scale


def _conversion_rate(day: date, country: str, channel: str) -> float:
    cvr = BASE_CVR[channel] * COUNTRY_CVR_FACTOR[country]
    if country == "DE" and channel == "paid_search" and day >= DE_CVR_STEP_FROM:
        cvr *= 0.65
    return cvr


def _order_value(day: date, country: str, channel: str) -> float:
    aov = BASE_AOV[country] * CHANNEL_AOV_FACTOR[channel]
    if country == "US" and channel == "affiliate" and AFFILIATE_SHOCK[0] <= day <= AFFILIATE_SHOCK[1]:
        aov *= 0.55
    return aov


def generate_rows(
    seed: int = DEFAULT_SEED,
    start: date = START_DATE,
    end: date = END_DATE,
) -> list[Row]:
    """Build the full dataset in memory. Deterministic in ``seed``."""
    rng = Random(seed)
    rows: list[Row] = []
    day = start
    day_index = 0
    while day <= end:
        day_shock = rng.lognormvariate(0.0, DAY_SHOCK_SIGMA)
        # Conversion moves site-wide day to day as well; without this a rate
        # metric would be almost noiseless, because the traffic shock cancels
        # between its numerator and denominator.
        cvr_day_shock = rng.lognormvariate(0.0, DAY_SHOCK_SIGMA / 2)
        for country in COUNTRIES:
            for channel in CHANNELS:
                scale = _session_scale(day, country, channel, day_index)
                if scale <= 0.0:
                    continue  # no rows at all: a genuinely absent cell
                scale *= day_shock
                sessions = int(round(scale * rng.lognormvariate(0.0, SESSION_NOISE_SIGMA)))
                if sessions <= 0:
                    continue
                cvr = (
                    _conversion_rate(day, country, channel)
                    * cvr_day_shock
                    * rng.lognormvariate(0.0, CVR_NOISE_SIGMA)
                )
                orders = int(round(sessions * min(cvr, 0.9)))
                aov = _order_value(day, country, channel) * rng.lognormvariate(
                    0.0, AOV_NOISE_SIGMA
                )
                gmv = round(orders * aov, 2)
                rows.append(Row(day, country, channel, sessions, orders, gmv))
        day += timedelta(days=1)
        day_index += 1
    return rows


def write_csv(path: Path, rows: list[Row]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(FIELDNAMES)
        for row in rows:
            writer.writerow(
                [
                    row.day.isoformat(),
                    row.country,
                    row.channel,
                    row.sessions,
                    row.orders,
                    f"{row.gmv:.2f}",
                ]
            )
    return path


def generate_file(path: Path, seed: int = DEFAULT_SEED) -> tuple[Path, int]:
    rows = generate_rows(seed=seed)
    write_csv(path, rows)
    return path, len(rows)
