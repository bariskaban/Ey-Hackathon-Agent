"""Eval results: 20 test invoices with known answers, several runs each, per model."""
from __future__ import annotations

from collections import Counter
from typing import Any

from nicegui import ui

from ui.components.widgets import error_label, page_header
from ui.services import open_case_count, svc
from ui.theme import VERDICTS, esc, fmt_date, html, key, shell

MODELS = {"qwen3:8b": "qwen3:8b", "gpt-oss:20b": "gpt-oss:20b", "qwen3:30b-a3b": "qwen3:30b-a3b"}
TICK_CH, CROSS_CH, DASH = "\u2713", "\u2715", "\u2013"
WHERE = {"qwen3:8b": "All on the GPU", "gpt-oss:20b": "GPU and system memory", "qwen3:30b-a3b": "GPU and system memory"}


def _summary(report: Any) -> dict[str, Any]:
    rows = report.rows
    correct = consistent = caught = planted = 0
    secs, calls = [], []
    for r in rows:
        runs = [key(x) for x in r.runs]
        majority = Counter(runs).most_common(1)[0][0]
        correct += majority == key(r.expected_verdict)
        consistent += len(set(runs)) == 1
        if key(r.planted_error) != "CLEAN":
            planted += 1
            caught += majority == key(r.expected_verdict)
        secs.append(r.seconds)
        calls.append(getattr(r, "tool_calls", 0) or 0)
    n = len(rows) or 1
    return {"n": len(rows), "correct": correct, "consistent": consistent, "caught": caught, "planted": planted,
            "avg_s": round(sum(secs) / n), "avg_calls": round(sum(calls) / n, 1)}


@ui.page("/eval")
async def eval_page() -> None:
    status = await svc.system_status()
    with shell("eval", status, await open_case_count()):
        header = ui.element("div").classes("vr-header")
        progress = html("")
        body = ui.element("div").style("display:flex;flex-direction:column;gap:22px")
        state = {"running": False}

        async def draw() -> None:
            reports = await svc.list_eval_runs()
            body.clear()
            with body:
                if not reports:
                    html('<div class="vr-empty">No eval runs yet. Choose a model and select "Run eval".</div>')
                    return
                latest = reports[0]
                s = _summary(latest)
                safe = [r.doc_id for r in latest.rows if getattr(r, "outcome", None) == "safe_fail"]
                misses = [r.doc_id for r in latest.rows if r.doc_id not in safe
                          and Counter(key(x) for x in r.runs).most_common(1)[0][0] != key(r.expected_verdict)]
                flips = [r.doc_id for r in latest.rows if len({key(x) for x in r.runs}) > 1]
                heads = [
                    ("Correct verdicts", s["correct"], f" of {s['n']}", f"Majority verdict across {latest.runs_per_invoice} runs"
                     + (f". Sent to a person instead of guessing: {', '.join(safe)}" if safe else "")),
                    ("Planted errors caught", s["caught"], f" of {s['planted']}", "Missed: " + ", ".join(misses) if misses else "None missed"),
                    (f"Same verdict in all {latest.runs_per_invoice} runs", s["consistent"], f" of {s['n']}",
                     "Changed: " + ", ".join(flips) if flips else "No verdict changed between runs"),
                    ("Average time per invoice", s["avg_s"], " s", f"{esc(latest.model)}, {s['avg_calls']} tool calls each"),
                ]
                sample = ('<p style="margin:0;font-weight:700;color:#1D4ED8">! Sample numbers from the mock service, '
                          'not a real run.</p>' if getattr(latest, "sample", False) else "")
                html(sample + '<section aria-label="Headline results" class="vr-grid" style="grid-template-columns:repeat(auto-fit,minmax(210px,1fr))">'
                     + "".join(f'<div class="vr-card" style="padding:18px 20px;display:flex;flex-direction:column;gap:4px">'
                               f'<span style="font-size:14px;color:var(--vr-body2)">{label}</span>'
                               f'<span style="font-family:var(--vr-display);font-size:42px;font-weight:600;line-height:1.1">{big}'
                               f'<span style="font-size:22px;color:var(--vr-graphite)">{of}</span></span>'
                               f'<span class="vr-small">{note}</span></div>' for label, big, of, note in heads)
                     + "</section>")

                ok = VERDICTS["MATCHED"]
                bad = VERDICTS["MISMATCH"]
                rows = []
                for r in latest.rows:
                    cells = []
                    for x in r.runs:
                        good = key(x) == key(r.expected_verdict)
                        careful = not good and getattr(r, "outcome", None) == "safe_fail" and key(x) == "NEEDS_REVIEW"
                        c = ok if good else VERDICTS["NEEDS_REVIEW"] if careful else bad
                        word = ("Correct" if good else "Safe: sent to a person" if careful
                                else f"Wrong: {VERDICTS.get(key(x), {}).get('label', x)}")
                        cells.append(f'<td style="text-align:center"><span role="img" aria-label="{word}" title="{word}" '
                                     f'style="display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;'
                                     f'border-radius:4px;border:1.5px solid {c["base"]};background:{c["tint"]};color:{c["text"]};'
                                     f'font-weight:700">{TICK_CH if good else "!" if careful else CROSS_CH}</span></td>')
                    wrong = r.doc_id not in safe and any(key(x) != key(r.expected_verdict) for x in r.runs)
                    exp = VERDICTS.get(key(r.expected_verdict), {}).get("label", r.expected_verdict)
                    rows.append(f'<tr class="{"vr-bad" if wrong else ""}" data-href="/verdict/{esc(r.doc_id)}" '
                                f'onclick="if(!event.target.closest(\'a\'))location.href=this.dataset.href" style="cursor:pointer">'
                                f'<td><a class="vr-num" href="/verdict/{esc(r.doc_id)}">{esc(r.doc_id)}</a></td>'
                                f'<td>{esc(error_label(r.planted_error))}</td><td>{esc(exp)}</td>{"".join(cells)}'
                                f'<td class="vr-num" style="color:var(--vr-body2)">{esc(r.caught_by or DASH)}</td>'
                                f'<td class="vr-num vr-right">{r.seconds}</td></tr>')
                run_heads = "".join(f'<th style="text-align:center">Run {i}</th>' for i in range(1, latest.runs_per_invoice + 1))
                html('<section aria-label="Results per invoice" class="vr-card"><div class="vr-card-head">'
                     '<h2 class="vr-h2">Every invoice, every run</h2><span class="vr-small">'
                     f'{esc(latest.model)}, finished {fmt_date(latest.finished_at)}. Select an invoice to open its verdict.</span></div>'
                     '<div class="vr-table-wrap"><table class="vr-table"><thead><tr><th>Invoice</th><th>Planted error</th>'
                     f'<th>Expected verdict</th>{run_heads}<th>Caught by</th><th class="vr-right">Seconds</th></tr></thead>'
                     f'<tbody>{"".join(rows)}</tbody></table></div></section>')

                best: dict[str, Any] = {}
                for rep in reports:  # newest first, so the first per model wins
                    best.setdefault(key(rep.model), rep)
                cmp_rows = []
                for m in MODELS:
                    rep = best.get(m)
                    if rep is None:
                        cmp_rows.append(f'<tr><td class="vr-num" style="font-weight:500">{m}</td><td>{WHERE[m]}</td>'
                                        f'<td colspan="5" class="vr-small">Not run yet</td></tr>')
                        continue
                    t = _summary(rep)
                    cmp_rows.append(f'<tr><td class="vr-num" style="font-weight:500">{m}</td><td>{WHERE[m]}</td>'
                                    f'<td class="vr-num vr-right">{t["correct"]} of {t["n"]}</td>'
                                    f'<td class="vr-num vr-right">{t["caught"]} of {t["planted"]}</td>'
                                    f'<td class="vr-num vr-right">{t["consistent"]} of {t["n"]}</td>'
                                    f'<td class="vr-num vr-right">{t["avg_s"]}</td><td class="vr-num vr-right">{t["avg_calls"]}</td></tr>')
                html('<section aria-label="Model comparison" class="vr-card"><div class="vr-card-head"><h2 class="vr-h2">Model comparison</h2></div>'
                     '<div class="vr-table-wrap"><table class="vr-table"><thead><tr><th>Model</th><th>Where it runs</th>'
                     '<th class="vr-right">Correct</th><th class="vr-right">Errors caught</th><th class="vr-right">Consistent</th>'
                     '<th class="vr-right">Seconds per invoice</th><th class="vr-right">Tool calls per invoice</th></tr></thead>'
                     f'<tbody>{"".join(cmp_rows)}</tbody></table></div></section>')

        async def run() -> None:
            if state["running"]:
                return
            state["running"] = True
            run_btn.set_enabled(False)
            try:
                async for p in svc.run_eval(model.value, int(runs.value)):
                    pct = round(p.done / p.total * 100) if p.total else 0
                    progress.set_content(
                        f'<div class="vr-card" style="padding:14px 18px;display:flex;flex-wrap:wrap;gap:16px;align-items:center">'
                        f'<strong style="font-size:14px">{"Finished" if p.finished else "Running"}</strong>'
                        f'<div class="vr-bar" style="flex:1 1 260px" role="progressbar" aria-valuenow="{pct}" aria-valuemin="0" aria-valuemax="100">'
                        f'<div style="width:{pct}%"></div></div>'
                        f'<span class="vr-num" style="font-size:14px">{p.done} of {p.total} runs</span>'
                        f'<span class="vr-small">{esc(p.message)}</span></div>')
                ui.notify("Eval finished.", type="positive")
                await draw()
            except Exception as exc:
                ui.notify(f"The eval stopped: {exc}", type="negative")
            finally:
                state["running"] = False
                run_btn.set_enabled(True)

        with header:
            html(page_header("Eval results", "20 test invoices with known answers, run several times each."))
            with ui.element("div").style("display:flex;flex-wrap:wrap;gap:12px;align-items:center"):
                model = ui.select(MODELS, value="qwen3:8b", label="Model").props("outlined dense").style("min-width:190px")
                runs = ui.select({1: "1", 3: "3", 5: "5"}, value=3, label="Runs per invoice").props("outlined dense").style("min-width:140px")
                run_btn = ui.button("Run eval", on_click=run).props("no-caps unelevated").classes("vr-btn vr-btn-primary")

        await draw()
