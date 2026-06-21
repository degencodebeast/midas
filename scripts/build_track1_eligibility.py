import json
from pathlib import Path


source = Path(__file__).resolve().parents[2] / "TOKENS.MD"
text = source.read_text(encoding="utf-8")
# The authority list is preceded by a descriptive sentence ("a fixed list of
# BEP-20 tokens ... (149 tokens)."). Splitting on the literal "Eligible tokens:"
# marker folds that prose into the first symbol, corrupting track1-001. Anchor on
# the "(149 tokens)." clause instead so the first symbol is the clean ETH entry,
# matching the authoritative research artifact (ETH at track1-001, SLX at 089/107).
symbols = [item.strip() for item in text.split("(149 tokens).", 1)[1].split(" Trades outside", 1)[0].split(",")]
if len(symbols) != 149:
    raise SystemExit(f"expected 149 eligibility rows, got {len(symbols)}")
rows = [
    {"eligibility_id": f"track1-{index:03d}", "competition_symbol": symbol}
    for index, symbol in enumerate(symbols, start=1)
]
target = Path(__file__).resolve().parents[1] / "data" / "track1_eligibility.json"
target.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
