"""The audit trail: claim -> fact id -> computation.

The report is for reading; this file is for checking. Every published claim is
listed with the fact it binds to, the value the pipeline computed, the tolerance
applied, and the derivation chain of that fact back to the source rows. Anyone
holding the audit trail and the source file can re-derive every number in the
report without trusting the report -- or this pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .facts import FactBook
from .validator import ValidationReport

SCHEMA_VERSION = "1.0"


def _provenance_chain(book: FactBook, fact_id: str, depth: int = 3) -> list[dict]:
    """Walk a fact's inputs back toward the source, breadth-first, bounded."""
    chain: list[dict] = []
    seen: set[str] = set()
    frontier = [fact_id]
    for _ in range(depth):
        next_frontier: list[str] = []
        for current in frontier:
            fact = book.get(current)
            if fact is None or current in seen:
                continue
            seen.add(current)
            chain.append(
                {
                    "fact_id": fact.id,
                    "computation": fact.provenance.computation,
                    "formula": fact.provenance.formula,
                    "inputs": list(fact.provenance.inputs),
                    "note": fact.provenance.note,
                    "flags": list(fact.flags),
                }
            )
            next_frontier.extend(i for i in fact.provenance.inputs if i in book.facts)
        frontier = next_frontier
        if not frontier:
            break
    return chain


def build_audit(
    book: FactBook,
    report: ValidationReport,
    payload: dict | None,
    inputs: dict,
    provider: dict,
    attempts: list[dict],
) -> dict:
    """Assemble the machine-readable audit trail for one run."""
    highlights = (payload or {}).get("highlights", [])

    claims = []
    for binding in report.bindings:
        highlight = (
            highlights[binding.highlight_index]
            if binding.highlight_index < len(highlights)
            else {}
        )
        fact = book.get(binding.fact_id)
        claims.append(
            {
                **binding.to_dict(),
                "highlight_title": highlight.get("title"),
                "metric_id": highlight.get("metric_id"),
                "fact_label": fact.label if fact else None,
                "display": fact.display if fact else None,
                "unit": fact.unit if fact else None,
                "period": (
                    {
                        "type": fact.period_type,
                        "start": fact.period_start,
                        "end": fact.period_end,
                    }
                    if fact
                    else None
                ),
                "derivation": _provenance_chain(book, binding.fact_id) if fact else [],
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": book.run_id,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "as_of": book.as_of,
        "week_in_review": {"start": book.week_start, "end": book.week_end},
        "partial_week": book.partial_week,
        "incomplete": book.incomplete,
        "inputs": inputs,
        "writer": {**provider, "attempts": attempts},
        "coverage": book.coverage,
        "shortlist": [item.to_dict() for item in book.shortlist],
        "validation": report.to_dict(),
        "claims": claims,
        "notes": book.notes,
    }
