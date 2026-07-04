"""Вывод отчёта: консоль (таблицы) + JSON + Markdown + автономный HTML."""

from __future__ import annotations

from pathlib import Path

from .models import RunReport


def _bar(x: float, width: int = 10) -> str:
    n = int(round(x * width))
    return "█" * n + "·" * (width - n)


def print_console(rep: RunReport) -> None:
    o = rep.overall
    c = rep.components
    print("\n" + "═" * 74)
    print(f"  BENCHMARK · {rep.base_url}  ·  режимы: {', '.join(rep.modes)}")
    print("═" * 74)
    comp = (
        f"backend={'up' if c.backend else 'DOWN'}"
        f"  ontology_kg={c.ontology_kg}  science_kg={c.science_kg}"
    )
    print(f"  Компоненты: {comp}")
    print(
        f"  Вопросов: {o.get('n_total')}  ok: {o.get('n_ok')}  "
        f"ошибок: {o.get('n_error')}"
    )
    print(
        f"  ИТОГ score: {o.get('score'):.3f}  {_bar(o.get('score', 0))}   "
        f"pass@{o.get('pass_threshold')}: {o.get('n_passed')}/{o.get('n_ok')} "
        f"({o.get('pass_rate', 0) * 100:.0f}%)"
    )

    prov = o.get("provenance") or {}
    if prov:
        print(
            f"  Провенанс: ссылка на источник в {prov.get('with_source')}/"
            f"{prov.get('answerable')} ответов "
            f"({prov.get('source_coverage', 0) * 100:.0f}%)  ·  "
            f"цитата-док в {prov.get('citation_coverage', 0) * 100:.0f}%"
        )

    lat = o.get("latency") or {}
    if lat:
        print(
            f"  Латентность: p50 {lat.get('p50')}c  p95 {lat.get('p95')}c  "
            f"max {lat.get('max')}c  (в цель ≤{lat.get('target_s')}c: "
            f"{lat.get('within_target')}/{o.get('n_ok')})"
        )

    bm = o.get("by_metric") or {}
    if bm:
        print("\n  По метрикам:")
        for name in ("keywords", "numeric", "provenance", "mode", "latency", "honesty"):
            if name in bm:
                print(f"    {name:<12} {bm[name]:.3f}  {_bar(bm[name])}")

    bc = o.get("by_category") or {}
    if bc:
        print("\n  По категориям:")
        print(f"    {'категория':<24}{'n':>4}{'ok':>4}{'score':>8}{'pass':>7}")
        for cat, s in bc.items():
            print(
                f"    {cat:<24}{s['n']:>4}{s['ok']:>4}{s['score']:>8.3f}"
                f"{s['pass_rate'] * 100:>6.0f}%"
            )

    worst = sorted([r for r in rep.results if r.ok], key=lambda r: r.score)[:8]
    if worst:
        print("\n  Слабейшие вопросы:")
        for r in worst:
            print(f"    {r.score:.2f}  [{r.category}] {r.id}")
    errs = [r for r in rep.results if not r.ok]
    if errs:
        print(f"\n  Ошибки транспорта ({len(errs)}):")
        for r in errs[:8]:
            print(f"    {r.id}: {r.error}")
    print("═" * 74 + "\n")


def write_json(rep: RunReport, path: Path) -> None:
    path.write_text(rep.model_dump_json(indent=2), encoding="utf-8")


def write_markdown(rep: RunReport, path: Path) -> None:
    o = rep.overall
    lat = o.get("latency") or {}
    lines = [
        f"# Benchmark report — {rep.started_at}",
        "",
        f"- **Контур:** `{rep.base_url}`  ·  режимы: {', '.join(rep.modes)}",
        f"- **Компоненты:** backend={'up' if rep.components.backend else 'DOWN'}, "
        f"ontology_kg={rep.components.ontology_kg}, science_kg={rep.components.science_kg}",
        f"- **ИТОГ score:** **{o.get('score')}**  ·  "
        f"pass@{o.get('pass_threshold')}: {o.get('n_passed')}/{o.get('n_ok')} "
        f"({o.get('pass_rate', 0) * 100:.0f}%)  ·  ошибок: {o.get('n_error')}",
    ]
    if lat:
        lines.append(
            f"- **Латентность:** p50 {lat.get('p50')}c · p95 {lat.get('p95')}c "
            f"· max {lat.get('max')}c · цель ≤{lat.get('target_s')}c"
        )
    bm = o.get("by_metric") or {}
    if bm:
        lines += ["", "## По метрикам", "", "| метрика | среднее |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in bm.items()]
    bc = o.get("by_category") or {}
    if bc:
        lines += [
            "",
            "## По категориям",
            "",
            "| категория | n | ok | score | pass |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {cat} | {s['n']} | {s['ok']} | {s['score']} | "
            f"{s['pass_rate'] * 100:.0f}% |"
            for cat, s in bc.items()
        ]
    lines += [
        "",
        "## Вопросы",
        "",
        "| score | категория | id | mode_used | цит | числа | пат | лат,c |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rep.results, key=lambda r: (r.category, -r.score)):
        lines.append(
            f"| {r.score:.2f} | {r.category} | {r.id} | {r.mode_used or '—'} | "
            f"{r.n_citations} | {r.n_numeric} | {'да' if r.has_patent else '—'} | "
            f"{r.latency_s:.1f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_html(rep: RunReport, path: Path) -> None:
    o = rep.overall
    rows = "".join(
        f"<tr><td>{r.score:.2f}</td><td>{r.category}</td><td>{r.id}</td>"
        f"<td>{r.mode_used or '—'}</td><td>{r.n_citations}</td><td>{r.n_numeric}</td>"
        f"<td>{'✓' if r.has_patent else ''}</td><td>{r.latency_s:.1f}</td></tr>"
        for r in sorted(rep.results, key=lambda r: (r.category, -r.score))
    )
    cats = "".join(
        f"<tr><td>{c}</td><td>{s['n']}</td><td>{s['ok']}</td>"
        f"<td>{s['score']}</td><td>{s['pass_rate'] * 100:.0f}%</td></tr>"
        for c, s in (o.get("by_category") or {}).items()
    )
    html = f"""<!doctype html><meta charset=utf-8>
<title>Benchmark {rep.started_at}</title>
<style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:2rem;color:#111}}
 h1{{font-size:1.3rem}} table{{border-collapse:collapse;margin:1rem 0;width:100%}}
 td,th{{border:1px solid #ddd;padding:4px 8px;text-align:left}}
 th{{background:#f4f4f5}} .big{{font-size:2rem;font-weight:700}}
 code{{background:#f4f4f5;padding:1px 4px;border-radius:3px}}
</style>
<h1>Benchmark report — {rep.started_at}</h1>
<p>Контур <code>{rep.base_url}</code> · режимы {", ".join(rep.modes)} ·
backend={"up" if rep.components.backend else "DOWN"} ontology_kg={rep.components.ontology_kg}
science_kg={rep.components.science_kg}</p>
<p class=big>{o.get("score")}</p>
<p>pass@{o.get("pass_threshold")}: {o.get("n_passed")}/{o.get("n_ok")}
({o.get("pass_rate", 0) * 100:.0f}%) · ошибок {o.get("n_error")}</p>
<h2>По категориям</h2>
<table><tr><th>категория</th><th>n</th><th>ok</th><th>score</th><th>pass</th></tr>{cats}</table>
<h2>Вопросы</h2>
<table><tr><th>score</th><th>категория</th><th>id</th><th>mode_used</th><th>цит</th>
<th>числа</th><th>пат</th><th>лат,c</th></tr>{rows}</table>
"""
    path.write_text(html, encoding="utf-8")


def write_all(rep: RunReport, out_dir: Path, stamp: str) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out_dir / f"run_{stamp}.json",
        "md": out_dir / f"report_{stamp}.md",
        "html": out_dir / f"report_{stamp}.html",
    }
    write_json(rep, paths["json"])
    write_markdown(rep, paths["md"])
    write_html(rep, paths["html"])
    return paths
