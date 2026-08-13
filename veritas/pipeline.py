"""End-to-end orchestration: source -> facts -> draft -> validation -> report.

The failure policy lives here, and it is fail-closed at both ends. A required
metric that could not be computed marks the run INCOMPLETE, stamps a banner on
the report and exits non-zero. A draft that fails validation is sent back to the
writer with the violation list; after ``max_retries`` it fails loudly, writes the
audit trail anyway, and produces no report. A silent partial success is the one
outcome this pipeline will not produce.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .audit import build_audit
from .compute import build_plan, compute, load_source
from .facts import FactBook, build_factbook
from .llm import LLMProvider, get_provider
from .registry import load_registry
from .render import render_report
from .validator import ValidationReport, validate_response
from .writer import SYSTEM_PROMPT, WriterInput, build_user_prompt, load_brief

DEFAULT_AS_OF = date(2026, 8, 13)
DEFAULT_MAX_RETRIES = 2

FACTS_FILENAME = "facts.json"
PACK_FILENAME = "pack.json"
REPORT_FILENAME = "report.md"
AUDIT_FILENAME = "audit.json"

EXIT_OK = 0
EXIT_VALIDATION_FAILED = 2
EXIT_INCOMPLETE = 3


class ValidationFailure(RuntimeError):
    """The writer could not produce a valid draft within the retry budget."""

    def __init__(self, report: ValidationReport, attempts: int) -> None:
        codes = sorted({v.code for v in report.violations})
        super().__init__(
            f"validation failed after {attempts} attempt(s); "
            f"unresolved violations: {', '.join(codes)}"
        )
        self.report = report
        self.attempts = attempts


@dataclass(frozen=True)
class RunConfig:
    data_path: Path
    registry_path: Path
    brief_path: Path
    out_dir: Path
    as_of: date = DEFAULT_AS_OF
    provider_name: str = "mock"
    model: str | None = None
    max_retries: int = DEFAULT_MAX_RETRIES


@dataclass
class RunResult:
    book: FactBook
    payload: dict
    report: ValidationReport
    audit: dict
    markdown: str
    attempts: list[dict] = field(default_factory=list)
    written: dict[str, Path] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        if not self.report.passed:
            return EXIT_VALIDATION_FAILED
        if self.book.incomplete:
            return EXIT_INCOMPLETE
        return EXIT_OK


def run(config: RunConfig, provider: LLMProvider | None = None) -> RunResult:
    """Execute one full reporting run."""
    registry = load_registry(config.registry_path)
    source = load_source(config.data_path)
    plan = build_plan(config.as_of)
    computed = compute(source, registry, plan)

    run_id = _run_id(source.sha256, registry.sha256, config.as_of)
    book = build_factbook(computed, registry, plan, source.sha256, run_id)

    config.out_dir.mkdir(parents=True, exist_ok=True)
    written = {
        "facts": _write_json(config.out_dir / FACTS_FILENAME, book.to_dict()),
        "pack": _write_json(config.out_dir / PACK_FILENAME, book.pack()),
    }

    writer = provider or get_provider(config.provider_name, config.model)
    brief = load_brief(config.brief_path)
    payload, report, attempts = _draft_until_valid(writer, book, registry, brief, config.max_retries)

    inputs = {
        "data_file": _portable_path(config.data_path),
        "data_sha256": source.sha256,
        "rows": source.row_count,
        "data_range": [source.min_date.isoformat(), source.max_date.isoformat()],
        "registry_file": _portable_path(config.registry_path),
        "registry_sha256": registry.sha256,
        "brief_file": _portable_path(config.brief_path),
    }
    provider_meta = {"provider": writer.name, "model": writer.model}
    audit = build_audit(book, report, payload, inputs, provider_meta, attempts)

    if not report.passed:
        written["audit"] = _write_json(config.out_dir / AUDIT_FILENAME, audit)
        raise ValidationFailure(report, len(attempts))

    rendered = render_report(
        payload,
        book,
        {**provider_meta, "attempts": len(attempts), "audit_filename": AUDIT_FILENAME},
    )
    audit["render"] = {
        "redactions": rendered.redactions,
        "highlights_rendered": rendered.highlight_count,
    }

    written["report"] = _write_text(config.out_dir / REPORT_FILENAME, rendered.markdown)
    written["audit"] = _write_json(config.out_dir / AUDIT_FILENAME, audit)

    return RunResult(
        book=book,
        payload=payload,
        report=report,
        audit=audit,
        markdown=rendered.markdown,
        attempts=attempts,
        written=written,
    )


def _draft_until_valid(
    writer: LLMProvider,
    book: FactBook,
    registry,
    brief: str,
    max_retries: int,
) -> tuple[dict | None, ValidationReport, list[dict]]:
    pack = book.pack()
    attempts: list[dict] = []
    violations: list[dict] | None = None
    payload: dict | None = None
    report = ValidationReport()

    for attempt in range(max_retries + 1):
        prompt = build_user_prompt(WriterInput(pack=pack, brief=brief, violations=violations))
        completion = writer.complete(SYSTEM_PROMPT, prompt)
        payload, report = validate_response(completion.text, book, registry)
        attempts.append(
            {
                "attempt": attempt + 1,
                "response_sha256": hashlib.sha256(completion.text.encode("utf-8")).hexdigest(),
                "passed": report.passed,
                "violations": [v.to_dict() for v in report.violations],
            }
        )
        if report.passed:
            break
        violations = report.feedback()

    return payload, report, attempts


def _portable_path(path: Path) -> str:
    """Record where a file came from without pinning it to one machine.

    The audit trail is meant to be shared, and an absolute path says more about
    the machine that produced it than about the run. Identity comes from the
    hash recorded alongside; the path is only a hint about where to look.
    """
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return path.name


def _run_id(source_sha: str, registry_sha: str, as_of: date) -> str:
    digest = hashlib.sha256(f"{source_sha}:{registry_sha}:{as_of.isoformat()}".encode()).hexdigest()
    return f"{as_of.isoformat()}-{digest[:10]}"


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_text(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path
