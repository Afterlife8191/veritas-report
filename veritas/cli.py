"""Command line entry point: ``python -m veritas <command>``."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .compute import SourceError
from .generate import DEFAULT_SEED, generate_file
from .llm import ProviderError
from .pipeline import (
    DEFAULT_AS_OF,
    DEFAULT_MAX_RETRIES,
    EXIT_INCOMPLETE,
    EXIT_VALIDATION_FAILED,
    RunConfig,
    ValidationFailure,
    run,
)
from .registry import RegistryError

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = REPO_ROOT / "data" / "storefront.csv"
DEFAULT_REGISTRY = REPO_ROOT / "config" / "registry.toml"
DEFAULT_BRIEF = REPO_ROOT / "config" / "audience_brief.md"
DEFAULT_OUT = REPO_ROOT / "out"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="veritas", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="write the synthetic source dataset")
    gen.add_argument("--out", type=Path, default=DEFAULT_DATA)
    gen.add_argument("--seed", type=int, default=DEFAULT_SEED)

    for name, help_text in (
        ("run", "compute facts, write the report, validate it"),
        ("demo", "generate data then run, in one command"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--data", type=Path, default=DEFAULT_DATA)
        cmd.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
        cmd.add_argument("--brief", type=Path, default=DEFAULT_BRIEF)
        cmd.add_argument("--out", type=Path, default=DEFAULT_OUT)
        cmd.add_argument(
            "--as-of",
            type=date.fromisoformat,
            default=DEFAULT_AS_OF,
            help="report as of this date; the day before it is the last day reported",
        )
        cmd.add_argument("--provider", choices=("mock", "anthropic"), default="mock")
        cmd.add_argument("--model", default=None, help="provider model id (anthropic only)")
        cmd.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
        if name == "demo":
            cmd.add_argument("--seed", type=int, default=DEFAULT_SEED)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "generate":
        path, rows = generate_file(args.out, seed=args.seed)
        print(f"wrote {rows:,} rows to {path}")
        return 0

    if args.command == "demo":
        path, rows = generate_file(args.data, seed=args.seed)
        print(f"generated {rows:,} rows -> {path}")

    config = RunConfig(
        data_path=args.data,
        registry_path=args.registry,
        brief_path=args.brief,
        out_dir=args.out,
        as_of=args.as_of,
        provider_name=args.provider,
        model=args.model,
        max_retries=args.max_retries,
    )

    try:
        result = run(config)
    except (SourceError, RegistryError) as exc:
        print(f"input rejected: {exc}", file=sys.stderr)
        return 1
    except ProviderError as exc:
        print(f"writer unavailable: {exc}", file=sys.stderr)
        return 1
    except ValidationFailure as exc:
        print(f"REPORT REJECTED: {exc}", file=sys.stderr)
        for violation in exc.report.violations:
            print(f"  [{violation.code}] {violation.message}", file=sys.stderr)
        print(f"audit trail written to {config.out_dir / 'audit.json'}", file=sys.stderr)
        return EXIT_VALIDATION_FAILED

    print(f"facts     {len(result.book.facts):,} computed, {len(result.book.pack_fact_ids()):,} in pack")
    print(f"writer    {result.audit['writer']['provider']} / {result.audit['writer']['model']}, "
          f"{len(result.attempts)} attempt(s)")
    print(f"validator {len(result.report.bindings)} claim(s) bound, 0 violations")
    print(f"report    {result.written['report']}")
    print(f"audit     {result.written['audit']}")
    if result.exit_code == EXIT_INCOMPLETE:
        print("run marked INCOMPLETE: a required metric was unusable", file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
