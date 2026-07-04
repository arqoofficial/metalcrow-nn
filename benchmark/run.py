"""Раннер бенчмарка: прогон датасета через контур чата + отчёт.

Примеры:
    python -m benchmark.run                       # auto-режим, локальный стек
    python -m benchmark.run --modes auto,ontology # матрица режимов
    python -m benchmark.run --only-category water_injection --judge
    python -m benchmark.run --offline             # только валидация датасета
    python -m benchmark.run --fail-under 0.6      # ненулевой exit для CI
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from . import evaluators as ev
from . import report as rep_mod
from . import scoring
from .client import ChatClient, ChatError, ChatTurn, probe_health
from .config import BenchConfig
from .dataset import dataset_stats, load_dataset
from .llm_judge import judge_answer
from .models import (
    ComponentHealth,
    CompetenceQuestion,
    QuestionResult,
    RunReport,
)


# ── оценка одного ответа ───────────────────────────────────────────────────


def evaluate_one(
    q: CompetenceQuestion,
    turn: ChatTurn,
    ask_mode: str,
    cfg: BenchConfig,
    use_judge: bool,
) -> QuestionResult:
    base = QuestionResult(
        id=q.id,
        category=q.category,
        question=q.question,
        ask_mode=ask_mode,
        lang=q.lang.value,
        difficulty=q.difficulty.value,
        answerable=q.answerable,
        weight=q.weight,
        ok=turn.ok,
        error=turn.error,
        latency_s=round(turn.latency_s, 3),
    )
    if not turn.ok:
        return base

    flat = ev.flatten_answer(turn.payload)
    base.mode_used = flat["mode_used"]
    base.tools_used = flat["tools_used"]
    base.answer_text = flat["text"]
    base.n_numeric = flat["n_numeric"]
    base.n_citations = flat["n_citations"]
    base.n_experiment_ids = flat["n_experiment_ids"]
    base.has_patent = flat["has_patent"]

    metrics = []
    if q.answerable:
        metrics.append(ev.eval_keywords(q, flat["text"]))
        metrics.append(ev.eval_numeric(q, flat["text"]))
        metrics.append(ev.eval_provenance(q, flat))
        metrics.append(ev.eval_mode(q, flat))
        metrics.append(ev.eval_latency(turn.latency_s, cfg.latency_target_s))
    else:
        metrics.append(ev.eval_honesty(q, flat))
        metrics.append(ev.eval_latency(turn.latency_s, cfg.latency_target_s))
    base.metrics = metrics
    base.score = round(scoring.score_question(q, metrics), 3)

    if use_judge:
        base.judge = judge_answer(cfg, q, flat["text"])
    return base


# ── прогон ─────────────────────────────────────────────────────────────────


def _send_mode(q: CompetenceQuestion, loop_mode: str, cfg: BenchConfig) -> str:
    if q.ask_mode is not None:
        return q.ask_mode.value
    if cfg.use_expected_mode:
        return q.expected_mode.value
    return loop_mode


def run(
    cfg: BenchConfig, questions: list[CompetenceQuestion], quiet: bool
) -> RunReport:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    health = probe_health(cfg)
    report = RunReport(
        started_at=started,
        base_url=cfg.base_url,
        modes=cfg.modes,
        dataset_path=str(cfg.dataset_path),
        n_questions=len(questions),
        components=ComponentHealth(
            backend=health.get("backend", False),
            ontology_kg=health.get("ontology_kg"),
            science_kg=health.get("science_kg"),
            detail=health,
        ),
    )
    if not health.get("backend"):
        print(
            f"[!] backend недоступен на {cfg.base_url} — проверьте, поднят ли стек "
            f"(docker compose up) и порт бэкенда.",
            file=sys.stderr,
        )

    client = ChatClient(cfg)
    try:
        client.login()
    except ChatError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        report.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        report.overall = scoring.aggregate([], cfg.pass_threshold, cfg.latency_target_s)
        return report

    multi = len(cfg.modes) > 1
    results: list[QuestionResult] = []
    try:
        for mode in cfg.modes:
            client.new_session(title=f"benchmark:{mode}")
            for i, q in enumerate(questions, 1):
                send = _send_mode(q, mode, cfg)
                turn = client.ask(q.question, mode=send)
                r = evaluate_one(q, turn, send, cfg, cfg.judge)
                if multi:
                    r.id = f"{q.id}@{mode}"
                results.append(r)
                if not quiet:
                    status = f"{r.score:.2f}" if turn.ok else "ERR"
                    print(
                        f"  [{mode}] {i:>3}/{len(questions)}  {status}  "
                        f"{r.latency_s:>5.1f}c  {r.mode_used or '—':<16} {q.id}"
                        + ("" if turn.ok else f"  ({r.error})")
                    )
    finally:
        client.close()

    report.results = results
    report.overall = scoring.aggregate(
        results, cfg.pass_threshold, cfg.latency_target_s
    )
    report.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return report


# ── фильтры и CLI ──────────────────────────────────────────────────────────


def _filter(
    qs: list[CompetenceQuestion], args: argparse.Namespace
) -> list[CompetenceQuestion]:
    if args.only_category:
        cats = {c.strip() for c in args.only_category.split(",")}
        qs = [q for q in qs if q.category in cats]
    if args.only_id:
        ids = {i.strip() for i in args.only_id.split(",")}
        qs = [q for q in qs if q.id in ids]
    if args.lang:
        qs = [q for q in qs if q.lang.value == args.lang]
    if args.difficulty:
        qs = [q for q in qs if q.difficulty.value == args.difficulty]
    if args.answerable_only:
        qs = [q for q in qs if q.answerable]
    if args.sample:
        qs = qs[: args.sample]
    return qs


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        "benchmark",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--base-url", help="URL бэкенда (default http://localhost:8000)")
    p.add_argument("--dataset", help="путь к questions.yaml")
    p.add_argument("--out-dir", help="куда писать отчёты (default benchmark/results)")
    p.add_argument(
        "--modes",
        default="auto",
        help="список режимов через запятую: auto,ontology,knowledge_graph",
    )
    p.add_argument(
        "--use-expected-mode",
        action="store_true",
        help="слать каждый вопрос в его expected_mode вместо --modes",
    )
    p.add_argument("--only-category")
    p.add_argument("--only-id")
    p.add_argument("--lang", choices=["ru", "en"])
    p.add_argument("--difficulty", choices=["easy", "medium", "hard"])
    p.add_argument("--answerable-only", action="store_true")
    p.add_argument(
        "--sample", type=int, help="первые N вопросов (для дымовых прогонов)"
    )
    p.add_argument("--latency-target", type=float, default=5.0)
    p.add_argument("--pass-threshold", type=float, default=0.6)
    p.add_argument(
        "--fail-under",
        type=float,
        help="ненулевой exit-code, если overall score ниже порога",
    )
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--no-reuse-session", action="store_true")
    p.add_argument(
        "--ontology-url", help="прямая проба ontology-kg health (если проброшен)"
    )
    p.add_argument(
        "--science-url", help="прямая проба science-kg health (если проброшен)"
    )
    p.add_argument("--judge", action="store_true", help="включить LLM-судью")
    p.add_argument("--judge-model")
    p.add_argument(
        "--offline",
        action="store_true",
        help="только валидация датасета и статистика, без обращения к сервису",
    )
    p.add_argument(
        "--list", action="store_true", help="показать статистику датасета и выйти"
    )
    p.add_argument("--quiet", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = BenchConfig.load(
        base_url=args.base_url,
        dataset_path=args.dataset,
        out_dir=args.out_dir,
        modes=[m.strip() for m in args.modes.split(",") if m.strip()],
        use_expected_mode=args.use_expected_mode,
        latency_target_s=args.latency_target,
        pass_threshold=args.pass_threshold,
        fail_under=args.fail_under,
        timeout_s=args.timeout,
        reuse_session=not args.no_reuse_session,
        ontology_url=args.ontology_url,
        science_url=args.science_url,
        judge=args.judge,
        judge_model=args.judge_model,
    )

    try:
        questions = load_dataset(cfg.dataset_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[x] датасет: {exc}", file=sys.stderr)
        return 2

    stats = dataset_stats(questions)
    if args.list or args.offline:
        print(f"Датасет: {cfg.dataset_path}")
        for k, v in stats.items():
            print(f"  {k}: {v}")
        if args.list:
            return 0

    questions = _filter(questions, args)
    if not questions:
        print("[x] после фильтров не осталось вопросов", file=sys.stderr)
        return 2

    if args.offline:
        print(
            f"\nOFFLINE: {len(questions)} вопрос(ов) прошли валидацию схемы. "
            f"Сервис не опрашивался."
        )
        return 0

    report = run(cfg, questions, quiet=args.quiet)
    rep_mod.print_console(report)

    stamp = report.started_at.replace(":", "").replace("-", "").replace("+0000", "")
    paths = rep_mod.write_all(report, cfg.out_dir, stamp)
    print("Отчёты:")
    for kind, path in paths.items():
        print(f"  {kind}: {path}")

    if cfg.fail_under is not None and report.overall.get("score", 0) < cfg.fail_under:
        print(
            f"\n[x] overall score {report.overall.get('score')} < "
            f"--fail-under {cfg.fail_under}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
