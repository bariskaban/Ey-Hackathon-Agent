"""Measure the model-based injection check on a set of injections and harmless look-alikes.

    python eval/injection_check.py eval/injection_sets/set_b.json eval/results/injection_b.json

Each set is a JSON file with "injections" (should be flagged) and "benign" (should not be). Every line is put
into the text of a real invoice and judged by the phrase list and by the model. Needs Ollama.

The two sets in eval/injection_sets/ have been used: set A before and after one prompt change, set B once after
it. To measure honestly again, write a new set; do not tune the prompt on one you will then quote.
"""
import asyncio
import json
import sys
import time
import warnings

warnings.filterwarnings("ignore")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from markitdown import MarkItDown

from agent import checks, injection
from agent.llm import LLM

fresh = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
base = MarkItDown().convert(str(ROOT / "data" / "invoices" / "INV-004.pdf")).text_content
def doc(extra):  # a real invoice text with one extra line before the payment line
    lines = base.splitlines()
    return "\n".join(lines[:-2] + [extra] + lines[-2:])
async def main():
    llm = LLM()
    out = {"injections": [], "benign": []}
    for kind in ("injections", "benign"):
        for text in fresh[kind]:
            t0 = time.time()
            phrase = bool(checks.INJECTION_RE.search(doc(text)))
            quote = await injection.find_injection(llm, doc(text))
            out[kind].append({"text": text, "phrase_list": phrase, "model_quote": quote, "s": round(time.time() - t0, 1)})
            flag = "model FLAGS" if quote else "model passes"
            print(f"{kind[:4]:4} phrase={'Y' if phrase else '-'} {flag:12} {time.time()-t0:4.1f}s  {text[:80]}", flush=True)
    inj = out["injections"]; ben = out["benign"]
    print(f"\ninjections: phrase list caught {sum(x['phrase_list'] for x in inj)}/10; model caught {sum(bool(x['model_quote']) for x in inj)}/10; "
          f"either {sum(x['phrase_list'] or bool(x['model_quote']) for x in inj)}/10")
    print(f"harmless: phrase list flagged {sum(x['phrase_list'] for x in ben)}/10; model flagged {sum(bool(x['model_quote']) for x in ben)}/10")
    # the same base invoice with nothing added
    print("base invoice alone, model flags:", bool(await injection.find_injection(llm, base)))
    Path(sys.argv[2]).write_text(json.dumps(out, indent=2), encoding="utf-8")
asyncio.run(main())
