"""Run the evaluation: every test invoice, several times, against the known answers.

    python eval/run_eval.py                       # all 20 invoices, 3 runs each, qwen3:8b
    python eval/run_eval.py --model <name>       # another model (pull it first with ollama)
    python eval/run_eval.py --runs 1 --no-agent   # quick check: one run, no model assessment
    python eval/run_eval.py INV-004 INV-018       # selected invoices

Needs the data (data/generate.py, data/render_pdfs.py), the inbox (scripts/load_inbox.py) and Ollama
running. Each run is saved to data/runs/eval/ and can be listed with agent.service.list_eval_runs().
Evaluation never opens review cases.
"""

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import evaluation, service
from agent.llm import DEFAULT_MODEL


async def main(args: argparse.Namespace) -> None:
    run_id = None
    try:
        async for progress in service.run_eval(args.model, args.runs, doc_ids=args.doc_ids or None,
                                               use_agent=not args.no_agent):
            run_id = progress.run_id
            if not progress.finished:
                print(f"[{progress.completed:3}/{progress.total}] {progress.invoice_id} run {progress.run_index}: "
                      f"{progress.last_result}", flush=True)
        report = next(r for r in await service.list_eval_runs() if r.run_id == run_id)
        print()
        print(evaluation.format_report(report))
    finally:
        await service.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("doc_ids", nargs="*", help="invoice ids to run (default: all)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--runs", type=int, default=3, help="runs per invoice (default 3)")
    parser.add_argument("--no-agent", action="store_true", help="skip the model's assessment (faster)")
    asyncio.run(main(parser.parse_args()))
