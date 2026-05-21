from pathlib import Path


def test_src_does_not_use_real_trading_or_private_api_calls():
    root = Path(__file__).resolve().parents[1] / "src"
    forbidden = ["create_order", "fetch_balance", "fetch_positions"]

    hits = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                hits.append((str(path), token))

    assert hits == []
