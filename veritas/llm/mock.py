"""A deterministic stand-in for a model.

The mock is not a simulation of a language model; it is a stand-in for the one
thing the pipeline asks a model to do -- *select* facts and narrate them. It
receives the same prompt a real provider does, recovers the pack from it, picks
candidates by simple rules, and writes prose using only the display strings the
pack supplies.

That makes the demo and the entire test suite runnable with no API key, and it
makes the validator's job honest: the mock is bound by exactly the same rules,
and it fails the validator if it breaks them.
"""

from __future__ import annotations

import json

from ..writer import extract_pack, extract_violations
from .base import Completion, LLMProvider

#: Selection thresholds and priorities. These decide what is worth *talking
#: about*; they compute nothing, and every number they compare against was
#: computed upstream.
ANOMALY_SCORE = 6.0
NOTABLE_Z = 2.5
NOTABLE_PCT = 8.0
#: A move too small to act on is not a highlight, however extreme its z-score:
#: a tight baseline makes a 1% wobble look significant.
MATERIAL_PCT = 3.0
MAX_HIGHLIGHTS = 6
MAX_PER_METRIC = 3
#: One segment, one story. The same shutdown seen through five metrics is one
#: piece of news, not five.
MAX_PER_CUT = 1

#: A segment that appeared or vanished outright is reported ahead of any
#: statistical move: it is a structural change, and no z-score describes it.
PRIORITY_DISAPPEARED = 1000.0
PRIORITY_NEW = 900.0
#: A week-long shift outranks a single-day blip of the same magnitude in a
#: weekly report -- but a genuinely extreme day still wins on strength alone.
PRIORITY_WEEKLY = 20.0
PRIORITY_ANOMALY = 0.0
#: One day's event is one story, however many series register it.
MAX_PER_DAY = 1

CAUSAL_HYPOTHESES = {
    "gmv": "Possibly driven by a pricing, promotion or mix change in this segment.",
    "orders": "Possibly driven by a demand or acquisition shift in this segment.",
    "sessions": "Possibly driven by a change in traffic acquisition.",
    "conversion_rate": "Possibly caused by a checkout, pricing or traffic-quality change.",
    "aov": "Possibly driven by a change in basket composition or discounting.",
}


def _fact_parts(fact_id: str) -> tuple[str, str, str, str]:
    metric, cut, period, statistic = fact_id.split("/")
    return metric, cut, period, statistic


class MockProvider(LLMProvider):
    """Deterministic writer. Same interface, same prompt, no network."""

    name = "mock"
    model = "deterministic-mock-writer-1"

    def complete(self, system: str, user: str) -> Completion:
        pack = extract_pack(user)
        rejected = {
            violation["highlight_index"]
            for violation in extract_violations(user)
            if violation.get("highlight_index") is not None
        }

        facts = {entry["id"]: entry for entry in pack["facts"]}
        week_period = f"complete_week:{pack['week_in_review']['end']}"

        # Score every candidate first, then take the best across all metrics.
        # Walking the shortlist in order and stopping at the cap would fill the
        # report with whichever metric happens to sort first.
        scored: list[tuple[float, int, dict]] = []
        dismissals: list[dict] = []

        for item in pack["shortlist"]:
            series = _series_facts(item, facts)
            candidate = _write_highlight(item, series, week_period, pack)
            if candidate is None:
                dismissals.append(
                    {
                        "shortlist_rank": item["rank"],
                        "reason_code": _dismissal_reason(series, week_period),
                    }
                )
                continue
            priority, highlight = candidate
            scored.append((priority, item["rank"], highlight))

        scored.sort(key=lambda entry: (-entry[0], entry[1]))
        highlights: list[dict] = []
        per_metric: dict[str, int] = {}
        per_cut: dict[str, int] = {}
        per_day: dict[str, int] = {}
        for _, rank, highlight in scored:
            metric_id = highlight["metric_id"]
            day = highlight.pop("_day", None)
            crowded = (
                per_metric.get(metric_id, 0) >= MAX_PER_METRIC
                or per_cut.get(highlight["cut"], 0) >= MAX_PER_CUT
                or (day is not None and per_day.get(day, 0) >= MAX_PER_DAY)
            )
            if crowded or len(highlights) >= MAX_HIGHLIGHTS:
                dismissals.append(
                    {
                        "shortlist_rank": rank,
                        "reason_code": "duplicate-of-highlight"
                        if crowded
                        else "small-absolute-impact",
                    }
                )
                continue
            per_metric[metric_id] = per_metric.get(metric_id, 0) + 1
            per_cut[highlight["cut"]] = per_cut.get(highlight["cut"], 0) + 1
            if day is not None:
                per_day[day] = per_day.get(day, 0) + 1
            highlights.append(highlight)

        # A rejected highlight is dropped rather than argued with: the validator
        # is the authority on whether a claim is admissible.
        highlights = [h for i, h in enumerate(highlights) if i not in rejected]

        payload = {"highlights": highlights, "dismissals": dismissals}
        return Completion(
            text=json.dumps(payload, indent=2),
            provider=self.name,
            model=self.model,
            metadata={"retry_feedback_seen": bool(rejected)},
        )


def _series_facts(item: dict, facts: dict[str, dict]) -> dict[tuple[str, str], dict]:
    series: dict[tuple[str, str], dict] = {}
    for fact_id in item["fact_ids"]:
        fact = facts.get(fact_id)
        if fact is None:
            continue
        _, _, period, statistic = _fact_parts(fact_id)
        series[(period, statistic)] = fact
    return series


def _value(series: dict, period: str, statistic: str) -> float | None:
    fact = series.get((period, statistic))
    return None if fact is None else fact.get("value")


def _anomaly_day(series: dict) -> str | None:
    """The most extreme scored day in this series, if any is extreme enough."""
    best_day, best_score = None, 0.0
    for (period, statistic), fact in series.items():
        if statistic != "robust_score" or not period.startswith("daily:"):
            continue
        value = fact.get("value")
        if value is not None and abs(value) >= max(ANOMALY_SCORE, best_score):
            best_day, best_score = period, abs(value)
    return best_day


def _write_highlight(
    item: dict, series: dict, week_period: str, pack: dict
) -> tuple[float, dict] | None:
    """Return ``(priority, highlight)`` for a candidate worth reporting."""
    metric_id = item["metric_id"]
    cut = item["cut"]
    metric_title = _metric_title(series, week_period, metric_id)

    if "disappeared" in item["selected_by"]:
        highlight = _disappearance_highlight(metric_id, metric_title, cut, series, week_period)
        if highlight is not None:
            return PRIORITY_DISAPPEARED, highlight

    if "newly_appeared" in item["selected_by"]:
        highlight = _arrival_highlight(metric_id, metric_title, cut, series, week_period, pack)
        if highlight is not None:
            return PRIORITY_NEW, highlight

    day = _anomaly_day(series)
    if day is not None:
        score = abs(series[(day, "robust_score")]["value"])
        highlight = _daily_highlight(metric_id, metric_title, cut, series, day)
        highlight["_day"] = day.split(":", 1)[1]
        return PRIORITY_ANOMALY + score, highlight

    z = _value(series, week_period, "screening_z")
    pct = _value(series, week_period, "wow_pct")
    strength = max(
        abs(z) if z is not None else 0.0,
        (abs(pct) / 2.0) if pct is not None else 0.0,
    )
    notable = (z is not None and abs(z) >= NOTABLE_Z) or (
        pct is not None and abs(pct) >= NOTABLE_PCT
    )
    material = pct is not None and abs(pct) >= MATERIAL_PCT
    if not (notable and material):
        return None
    highlight = _weekly_highlight(metric_id, metric_title, cut, series, week_period, pack)
    return (PRIORITY_WEEKLY + strength, highlight) if highlight is not None else None


def _metric_title(series: dict, week_period: str, metric_id: str) -> str:
    fact = series.get((week_period, "value"))
    if fact is None:
        return metric_id
    return fact["label"].split(" | ")[0]


def _claim(fact: dict) -> dict:
    return {"fact_id": fact["id"], "value": fact["value"]}


def _weekly_highlight(
    metric_id: str, title: str, cut: str, series: dict, week: str, pack: dict
) -> dict | None:
    value = series.get((week, "value"))
    pct = series.get((week, "wow_pct"))
    delta = series.get((week, "wow_delta"))
    baseline = series.get((week, "baseline_mean"))
    z = series.get((week, "screening_z"))
    if value is None or value["value"] is None or pct is None or pct["value"] is None:
        return None

    week_end = pack["week_in_review"]["end"]
    claims = [_claim(value), _claim(pct)]
    sentences = [
        f"{title} for {cut} came in at {value['display']} in the week ending {week_end}, "
        f"a week-over-week change of {pct['display']}."
    ]
    if delta is not None and delta["value"] is not None:
        claims.append(_claim(delta))
        sentences[0] = sentences[0][:-1] + f" ({delta['display']} in absolute terms)."
    if baseline is not None and baseline["value"] is not None:
        claims.append(_claim(baseline))
        tail = f"The trailing four-week baseline is {baseline['display']}"
        if z is not None and z["value"] is not None:
            claims.append(_claim(z))
            tail += f", putting this week at a screening z of {z['display']}"
        sentences.append(tail + ".")

    contribution = series.get((week, "contribution_share_pct"))
    if contribution is not None and contribution["value"] is not None:
        claims.append(_claim(contribution))
        sentences.append(
            f"This segment accounts for {contribution['display']} of the overall "
            f"week-over-week change."
        )

    flags = sorted({flag for fact in (value, pct) for flag in fact.get("flags", [])})
    if flags:
        sentences.append(f"Adequacy flags on this series: {', '.join(flags)}.")

    direction = "rose" if pct["value"] > 0 else "fell"
    return {
        "title": f"{title} {direction} in {cut}",
        "metric_id": metric_id,
        "cut": cut,
        "severity": _severity(abs(pct["value"]), z["value"] if z else None),
        "narrative": " ".join(sentences),
        "hypothesis": CAUSAL_HYPOTHESES.get(metric_id),
        "claims": claims,
    }


def _daily_highlight(metric_id: str, title: str, cut: str, series: dict, day: str) -> dict:
    day_date = day.split(":", 1)[1]
    value = series[(day, "value")]
    score = series[(day, "robust_score")]
    median = series.get((day, "weekday_median"))
    claims = [_claim(value), _claim(score)]
    shape = "spike" if score["value"] > 0 else "drop"
    sentence = (
        f"{title} for {cut} reached {value['display']} on {day_date}, "
        f"a robust score of {score['display']} against its same-weekday history."
    )
    if median is not None and median["value"] is not None:
        claims.append(_claim(median))
        sentence += f" The median of those prior same weekdays is {median['display']}."
    return {
        "title": f"Single-day {shape} in {title} for {cut}",
        "metric_id": metric_id,
        "cut": cut,
        "severity": "high",
        "narrative": sentence,
        "hypothesis": "One-day moves of this shape are usually caused by a campaign "
        "burst, a bot surge, or a tracking change; the pipeline cannot tell them apart.",
        "claims": claims,
    }


def _arrival_highlight(
    metric_id: str, title: str, cut: str, series: dict, week: str, pack: dict
) -> dict | None:
    value = series.get((week, "value"))
    if value is None or value["value"] is None:
        return None
    sentence = (
        f"{cut} recorded {value['display']} for {title} in the week ending "
        f"{pack['week_in_review']['end']}, its first appearance in the comparison "
        f"window. It has no baseline, so no comparison statistic exists for it yet."
    )
    return {
        "title": f"{cut} appeared for the first time",
        "metric_id": metric_id,
        "cut": cut,
        "severity": "medium",
        "narrative": sentence,
        "hypothesis": None,
        "claims": [_claim(value)],
    }


def _disappearance_highlight(
    metric_id: str, title: str, cut: str, series: dict, week: str
) -> dict | None:
    value = series.get((week, "value"))
    if value is None or value["value"] is None:
        # A rate for a segment with no traffic is null, not zero. There is no
        # number to report, so there is no highlight to write.
        return None
    claims = [_claim(value)]
    sentence = (
        f"{cut} recorded {value['display']} for {title} in the week under review, "
        f"having traded in every earlier week of the comparison window."
    )
    baseline = series.get((week, "baseline_mean"))
    if baseline is not None and baseline["value"] is not None:
        claims.append(_claim(baseline))
        sentence += f" Its trailing four-week baseline was {baseline['display']}."
    return {
        "title": f"{cut} stopped trading",
        "metric_id": metric_id,
        "cut": cut,
        "severity": "high",
        "narrative": sentence,
        "hypothesis": "A segment going to zero outright is usually caused by a "
        "feed, integration or tagging break rather than by demand.",
        "claims": claims,
    }


def _severity(pct_magnitude: float, z: float | None) -> str:
    if pct_magnitude >= 20 or (z is not None and abs(z) >= 3):
        return "high"
    if pct_magnitude >= 8 or (z is not None and abs(z) >= 2):
        return "medium"
    return "low"


def _dismissal_reason(series: dict, week: str) -> str:
    value = series.get((week, "value"))
    flags = set(value.get("flags", [])) if value else set()
    if {"no_baseline", "thin_baseline"} & flags:
        return "insufficient-baseline"
    if {"small_cell", "small_denominator", "no_exposure"} & flags:
        return "data-quality"
    pct = _value(series, week, "wow_pct")
    if pct is not None and abs(pct) < NOTABLE_PCT:
        return "within-baseline-variation"
    return "small-absolute-impact"


class ScriptedProvider(LLMProvider):
    """Returns pre-written responses in order. A test double, not a demo path.

    Used to drive the validator through adversarial outputs and to exercise the
    retry loop without depending on the mock writer's judgement.
    """

    name = "scripted"
    model = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> Completion:
        self.prompts.append(user)
        text = self._responses[min(len(self.prompts) - 1, len(self._responses) - 1)]
        return Completion(text=text, provider=self.name, model=self.model)
