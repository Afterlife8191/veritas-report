"""Markdown rendering.

Runs only on validated output. Every upstream string is treated as text, never
as markup: pipes are escaped so they cannot break out of a table cell, control
characters are dropped, and anything the validator would have rejected is
counted rather than silently swallowed -- a non-zero redaction count in the audit
trail means the sanitizer caught something the validator should have.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .facts import FactBook

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
MARKUP = re.compile(r"[<>]")
#: Characters that open a block-level construct when they lead a line -- a
#: heading, a list item, a quote. A narrative starting with one of these would
#: sit at the same level as the document's own structure. The validator already
#: rejects them, so anything escaped here is counted: a non-zero redaction count
#: means the sanitizer caught what the validator should have.
BLOCK_OPENER = re.compile(r"^\s{0,3}(#{1,6}\s|[-*+>|]\s|\d+\.\s)")

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Renderer:
    """Escapes untrusted strings and counts what it had to change."""

    redactions: int = 0

    def text(self, value: str) -> str:
        cleaned = CONTROL_CHARS.sub("", str(value)).replace("\n", " ").strip()
        replaced, count = MARKUP.subn("", cleaned)
        self.redactions += count
        if BLOCK_OPENER.match(replaced):
            self.redactions += 1
            replaced = "\\" + replaced.lstrip()
        return replaced

    def cell(self, value: str) -> str:
        return self.text(value).replace("|", "\\|")


@dataclass
class RenderResult:
    markdown: str
    redactions: int = 0
    highlight_count: int = 0
    warnings: list[str] = field(default_factory=list)


def render_report(payload: dict, book: FactBook, meta: dict) -> RenderResult:
    """Assemble the weekly report."""
    renderer = Renderer()
    highlights = sorted(
        payload.get("highlights", []),
        key=lambda h: SEVERITY_ORDER.get(h.get("severity"), 3),
    )
    lines: list[str] = []

    lines.append("# Weekly performance highlights")
    lines.append("")
    lines.append(
        f"Week under review **{book.week_start} to {book.week_end}** "
        f"(as of {book.as_of}). Run `{book.run_id}`."
    )
    lines.append("")

    if book.incomplete:
        lines.append(
            "> **INCOMPLETE RUN.** At least one required metric could not be "
            "computed. Highlights below cover only what was computable; see "
            "Coverage."
        )
        lines.append("")

    lines.append(
        "Every number below is bound to a deterministically computed fact. "
        f"The bindings are in the audit trail (`{meta.get('audit_filename', 'audit.json')}`)."
    )
    lines.append("")

    if not highlights:
        lines.append("## Summary")
        lines.append("")
        lines.append("Nothing this week cleared the bar for a highlight.")
        lines.append("")
    else:
        lines.append("## Summary")
        lines.append("")
        lines.append("| Severity | Metric | Segment | Headline |")
        lines.append("| --- | --- | --- | --- |")
        for highlight in highlights:
            lines.append(
                "| {severity} | {metric} | {cut} | {title} |".format(
                    severity=renderer.cell(highlight.get("severity", "")),
                    metric=renderer.cell(highlight.get("metric_id", "")),
                    cut=renderer.cell(highlight.get("cut", "")),
                    title=renderer.cell(highlight.get("title", "")),
                )
            )
        lines.append("")

        lines.append("## Highlights")
        lines.append("")
        for position, highlight in enumerate(highlights, start=1):
            lines.extend(_render_highlight(position, highlight, book, renderer))

    lines.extend(_render_coverage(book, renderer))
    lines.extend(_render_dismissals(payload.get("dismissals", []), book, renderer))
    lines.extend(_render_method(book, meta))

    return RenderResult(
        markdown="\n".join(lines).rstrip() + "\n",
        redactions=renderer.redactions,
        highlight_count=len(highlights),
    )


def _render_highlight(position: int, highlight: dict, book: FactBook, renderer: Renderer) -> list[str]:
    lines = [
        f"### {position}. {renderer.text(highlight.get('title', ''))}",
        "",
        f"*{renderer.text(highlight.get('metric_id', ''))} · "
        f"{renderer.text(highlight.get('cut', ''))} · "
        f"severity {renderer.text(highlight.get('severity', ''))}*",
        "",
        renderer.text(highlight.get("narrative", "")),
        "",
    ]

    hypothesis = highlight.get("hypothesis")
    if hypothesis:
        lines.append(f"**HYPOTHESIS (unverified):** {renderer.text(hypothesis)}")
        lines.append("")

    claims = [c for c in highlight.get("claims", []) if isinstance(c, dict)]
    if claims:
        lines.append("Evidence:")
        lines.append("")
        lines.append("| Fact | Value | Computation |")
        lines.append("| --- | --- | --- |")
        for claim in claims:
            fact = book.get(str(claim.get("fact_id")))
            if fact is None:
                continue
            lines.append(
                "| `{fid}` | {display} | {formula} |".format(
                    fid=renderer.cell(fact.id),
                    display=renderer.cell(fact.display),
                    formula=renderer.cell(fact.provenance.formula),
                )
            )
        lines.append("")
    return lines


def _render_coverage(book: FactBook, renderer: Renderer) -> list[str]:
    lines = ["## Coverage", "", "| Metric | Status | Required | Detail |", "| --- | --- | --- | --- |"]
    for entry in book.coverage:
        lines.append(
            "| {metric} | {status} | {required} | {detail} |".format(
                metric=renderer.cell(entry["metric_id"]),
                status=renderer.cell(entry["status"]),
                required="yes" if entry["required"] else "no",
                detail=renderer.cell(entry["detail"] or "-"),
            )
        )
    lines.append("")
    lines.append(
        "A metric that could not be computed is listed here rather than omitted: "
        "silence must never read as 'nothing happened'."
    )
    lines.append("")
    return lines


def _render_dismissals(dismissals, book: FactBook, renderer: Renderer) -> list[str]:
    if not dismissals:
        return []
    by_rank = {item.rank: item for item in book.shortlist}
    lines = [
        "## Considered and set aside",
        "",
        "| Candidate | Selected by | Reason |",
        "| --- | --- | --- |",
    ]
    for entry in dismissals:
        if not isinstance(entry, dict):
            continue
        item = by_rank.get(entry.get("shortlist_rank"))
        label = (
            f"{item.metric_id} · {'overall' if item.cut_dim is None else f'{item.cut_dim}={item.cut_value}'}"
            if item
            else f"rank {entry.get('shortlist_rank')}"
        )
        channels = ", ".join(item.channels) if item else "-"
        lines.append(
            f"| {renderer.cell(label)} | {renderer.cell(channels)} | "
            f"{renderer.cell(entry.get('reason_code', ''))} |"
        )
    lines.append("")
    return lines


def _render_method(book: FactBook, meta: dict) -> list[str]:
    return [
        "## How to read this",
        "",
        "- Weekly comparisons use complete Sunday-to-Saturday weeks. The current "
        "day is never included; a partial week is compared only against the same "
        "elapsed slice of earlier weeks.",
        "- `screening_z` and `robust_score` are ranking heuristics on short "
        "baselines, not calibrated significance tests.",
        "- Facts and hypotheses are separate. Anything under HYPOTHESIS is an "
        "unverified suggestion, not a finding.",
        "",
        f"Written by `{meta.get('provider')}` / `{meta.get('model')}` in "
        f"{meta.get('attempts', 1)} attempt(s); every claim re-checked against the "
        "computed facts before this document was produced.",
        "",
    ]
