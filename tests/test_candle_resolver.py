"""Unit tests for the multi-source candle resolver (_candle_candidates / _split_pair).

Verifies that user-typed symbols map to the *right* upstream sources in an accuracy-first
order with graceful fallback — the regression behind "chart unavailable … yahoo XAUUSDT ->
HTTP 429" (gold typed as a crypto pair fell through to Yahoo as a literal ticker and 429'd).

Run: .venv/bin/pytest tests/test_candle_resolver.py -q
"""
from __future__ import annotations

import pytest

from cryptodash.data.fetcher import _candle_candidates, _split_pair


def kinds(cands):
    return [k for k, _ in cands]


class TestCommodityResolution:
    def test_gold_spelled_as_crypto_pair_resolves_to_paxg_then_comex(self):
        # The reported bug: XAUUSDT must NOT be sent to Binance/Yahoo as a literal pair.
        c = _candle_candidates("XAUUSDT")
        assert ("binance", "PAXGUSDT") in c          # on-exchange gold token (1 PAXG ≈ 1 oz XAU)
        assert ("yahoo", "GC=F") in c                # precise COMEX front-month
        # the token proxy should be tried before the futures proxy for accuracy/availability
        assert kinds(c).index("binance") < kinds(c).index("yahoo")

    def test_gold_spelled_as_fx_pair(self):
        c = _candle_candidates("XAU/USD")
        assert ("yahoo", "GC=F") in c or ("binance", "PAXGUSDT") in c
        refs = {r for _, r in c}
        assert "GC=F" in refs and "PAXGUSDT" in refs

    def test_bare_gold_names(self):
        assert any(r == "GC=F" for _, r in _candle_candidates("GOLD"))
        assert any(r == "GC=F" for _, r in _candle_candidates("gold"))   # case-insensitive

    def test_silver(self):
        refs = {r for _, r in _candle_candidates("XAGUSDT")}
        assert "SI=F" in refs

    def test_brent_and_wti(self):
        assert any(r == "BZ=F" for _, r in _candle_candidates("BRENT"))
        assert any(r == "CL=F" for _, r in _candle_candidates("WTI"))


class TestCryptoResolution:
    def test_exact_usdt_pair_is_binance_first_then_yahoo_twin(self):
        c = _candle_candidates("ARBUSDT")
        assert ("binance", "ARBUSDT") in c
        assert ("yahoo", "ARB-USD") in c
        assert kinds(c).index("binance") == 0

    def test_slash_pair(self):
        c = _candle_candidates("BTC/USDC")
        assert ("binance", "BTCUSDC") in c
        # USDC quote → no -USD twin (not a USD pair), but the Binance spot is still first
        assert kinds(c)[0] == "binance"

    def test_bare_base_gets_usdt_spot_and_yahoo_twin(self):
        c = _candle_candidates("BTC")
        assert ("binance", "BTCUSDT") in c
        assert ("yahoo", "BTC-USD") in c


class TestUnknownAndEdge:
    def test_unknown_pair_tries_as_typed_on_both(self):
        c = _candle_candidates("NOPECOIN9XUSDT")
        assert ("binance", "NOPECOIN9XUSDT") in c
        assert kinds(c).count("yahoo") >= 1

    def test_stablecoin_quoted_commodity_is_not_remapped(self):
        # XAU/USDT is a stablecoin-quoted pair, not spot gold — leave it to the crypto path.
        c = _candle_candidates("XAU/USDT")
        assert ("binance", "XAUUSDT") in c or any(r == "GC=F" for _, r in c)

    def test_empty_symbol_returns_no_candidates(self):
        assert _candle_candidates("") == []


class TestSplitPair:
    @pytest.mark.parametrize("raw,base,quote", [
        ("BTC/USDT", "BTC", "USDT"),
        ("XAU/USD", "XAU", "USD"),
        ("ARBUSDT", "ARB", "USDT"),
        ("ETHUSDC", "ETH", "USDC"),
        ("BTC", "BTC", ""),
    ])
    def test_split(self, raw, base, quote):
        assert _split_pair(raw) == (base, quote)
