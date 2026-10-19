import unittest
from unittest.mock import patch

from src.skills.markets import (
    clear_markets_cache,
    get_daily_market_pulse,
    skill_markets_info,
)
from src.skills.markets.enums import MarketRegion, MarketView
from src.skills.markets.markets import (
    _compute_macro_regime_signals,
    _extract_candidate_ticker_query,
    _yahoo_agg,
)
from src.skills.markets.models import CryptoAssetQuote, TickerQuote
from src.utils.domain_vocab import TOOL_ARG_ALIASES


def _make_quote(
    symbol: str,
    name: str,
    price: float,
    change_pct: float,
    currency: str = "USD",
    exchange: str = "NMS",
    **kwargs,
) -> TickerQuote:
    change = round(price * (change_pct / 100.0), 2)
    defaults = {
        "symbol": symbol,
        "name": name,
        "currency": currency,
        "exchange": exchange,
        "market_state": "REGULAR",
        "price": price,
        "change": change,
        "change_percent": change_pct,
        "day_high": round(price * 1.015, 2),
        "day_low": round(price * 0.985, 2),
        "week52_high": round(price * 1.25, 2),
        "week52_low": round(price * 0.75, 2),
        "ma50": round(price * 0.95, 2),
        "ma200": round(price * 0.88, 2),
        "volume": 15_000_000,
        "avg_volume_3m": 10_000_000,
        "market_cap": 150_000_000_000,
        "trailing_pe": 28.4,
        "forward_pe": 22.1,
        "dividend_yield": 1.45,
        "earnings_timestamp": 1762992000,
    }
    defaults.update(kwargs)
    return TickerQuote(**defaults)


class TestMarketsSkill(unittest.TestCase):
    def setUp(self):
        clear_markets_cache()

    def tearDown(self):
        clear_markets_cache()

    def test_canadian_dual_listed_company_defaults_to_cad_tsx(self):
        """Canadian companies (Shopify, RBC, TD, Enbridge) must resolve to TSX (.TO) CAD primary + US secondary."""
        with patch("src.skills.markets.aggregators.yahoo._get_session_and_crumb", side_effect=RuntimeError("offline")):
            primary, dual, _ = _yahoo_agg.resolve_ticker_and_news("Shopify", prefer_canada=True)
            self.assertEqual(primary, "SHOP.TO")
            self.assertEqual(dual, "SHOP")

            primary_ry, dual_ry, _ = _yahoo_agg.resolve_ticker_and_news("royal bank", prefer_canada=True)
            self.assertEqual(primary_ry, "RY.TO")
            self.assertEqual(dual_ry, "RY")

            primary_enb, dual_enb, _ = _yahoo_agg.resolve_ticker_and_news("Enbridge", prefer_canada=True)
            self.assertEqual(primary_enb, "ENB.TO")
            self.assertEqual(dual_enb, "ENB")

    def test_single_ticker_snapshot_canadian_dual_listed_renders_cad_and_usd(self):
        """Verify skill_markets_info renders SHOP.TO (CAD) as primary and SHOP (USD) alongside it with technicals & news."""
        shop_to = _make_quote(
            "SHOP.TO",
            "Shopify Inc.",
            price=148.50,
            change_pct=3.45,
            currency="CAD",
            exchange="TOR",
        )
        shop_us = _make_quote(
            "SHOP",
            "Shopify Inc.",
            price=107.60,
            change_pct=3.41,
            currency="USD",
            exchange="NYQ",
        )
        fake_news = ["**Shopify GMV Surges Past Expectations in Q3** — *BNN Bloomberg* (https://example.com/shopify-q3)"]

        with (
            patch(
                "src.skills.markets.markets._yahoo_agg.resolve_ticker_and_news",
                return_value=("SHOP.TO", "SHOP", fake_news),
            ),
            patch(
                "src.skills.markets.markets._yahoo_agg.fetch_quotes",
                return_value={"SHOP.TO": shop_to, "SHOP": shop_us},
            ),
        ):
            output = skill_markets_info(ticker="Shopify")

        self.assertIn("TICKER INTELLIGENCE SNAPSHOT: Shopify Inc. (SHOP.TO)", output)
        self.assertIn("148.50 CAD", output)
        self.assertIn("Dual-Listed Quote (`SHOP` — NYQ):", output)
        self.assertIn("107.60 USD", output)
        self.assertIn("52-Week Range:", output)
        self.assertIn("Moving Averages & Trend:", output)
        self.assertIn("Bullish (Price > 50d & 200d MA)", output)
        self.assertIn("1.50x", output)
        self.assertIn("Shopify GMV Surges Past Expectations in Q3", output)

    def test_macro_regime_signals_detects_safe_haven_and_regional_divergence(self):
        """Verify _compute_macro_regime_signals flags VIX spikes, safe-haven rotation, and TSX outperformance."""
        quotes = {
            "^GSPC": _make_quote("^GSPC", "S&P 500", 5750.0, -1.45),
            "^IXIC": _make_quote("^IXIC", "Nasdaq Composite", 18100.0, -1.95),
            "^GSPTSE": _make_quote("^GSPTSE", "S&P/TSX Composite", 24600.0, 0.35, currency="CAD"),
            "^VIX": _make_quote("^VIX", "Cboe Volatility Index", 27.4, 14.2),
            "^TNX": _make_quote("^TNX", "US 10Y Treasury Yield", 4.42, 2.3),
            "GC=F": _make_quote("GC=F", "Gold Futures", 2745.0, 1.35),
            "SI=F": _make_quote("SI=F", "Silver Futures", 33.90, 2.10),
            "CL=F": _make_quote("CL=F", "WTI Crude Oil", 76.80, 3.10),
            "CADUSD=X": _make_quote("CADUSD=X", "CAD/USD", 0.7245, 0.12),
        }
        crypto = [
            CryptoAssetQuote(symbol="BTC", name="Bitcoin", price_usd=68500.0, price_cad=94500.0, change_24h_pct=2.4)
        ]
        signals = _compute_macro_regime_signals(quotes, crypto)
        joined = "\n".join(signals)
        self.assertIn("Risk-Off / Elevated Hedging", joined)
        self.assertIn("Regional Divergence", joined)
        self.assertIn("Safe-Haven Bid", joined)
        self.assertIn("WTI Crude Oil", joined)
        self.assertIn("Bitcoin (BTC)", joined)

    def test_global_overview_and_daily_market_pulse(self):
        """Verify overview and get_daily_market_pulse return structured global indices, commodities, movers, and crypto."""
        fake_quotes = {
            "^GSPTSE": _make_quote("^GSPTSE", "S&P/TSX Composite", 24800.0, 0.62, currency="CAD"),
            "CADUSD=X": _make_quote("CADUSD=X", "CAD/USD", 0.7245, 0.15),
            "USDCAD=X": _make_quote("USDCAD=X", "USD/CAD", 1.3802, -0.15, currency="CAD"),
            "^GSPC": _make_quote("^GSPC", "S&P 500", 5860.0, 0.45),
            "^IXIC": _make_quote("^IXIC", "Nasdaq Composite", 18520.0, 0.78),
            "^DJI": _make_quote("^DJI", "Dow Jones", 43250.0, 0.25),
            "^STOXX50E": _make_quote("^STOXX50E", "Euro Stoxx 50", 4985.0, 0.31, currency="EUR"),
            "^FTSE": _make_quote("^FTSE", "FTSE 100", 8350.0, 0.19, currency="GBP"),
            "^GDAXI": _make_quote("^GDAXI", "DAX", 19620.0, 0.44, currency="EUR"),
            "^N225": _make_quote("^N225", "Nikkei 225", 38950.0, -0.22, currency="JPY"),
            "^HSI": _make_quote("^HSI", "Hang Seng", 20800.0, 1.15, currency="HKD"),
            "GC=F": _make_quote("GC=F", "Gold Futures", 2730.0, 0.85),
            "SI=F": _make_quote("SI=F", "Silver Futures", 33.80, 1.90),
            "CL=F": _make_quote("CL=F", "WTI Crude Oil", 70.40, -1.10),
            "BZ=F": _make_quote("BZ=F", "Brent Crude Oil", 74.20, -0.95),
            "^VIX": _make_quote("^VIX", "VIX", 18.1, -3.5),
            "^TNX": _make_quote("^TNX", "10Y Yield", 4.08, -0.5),
        }
        fake_crypto = [
            CryptoAssetQuote(
                symbol="BTC",
                name="Bitcoin",
                price_usd=68450.0,
                price_cad=94475.0,
                change_24h_pct=2.35,
                market_cap_usd=1_350_000_000_000,
                volume_24h_usd=34_000_000_000,
            )
        ]
        fake_tsx_movers = {
            "gainers": [_make_quote("SHOP.TO", "Shopify Inc.", 148.50, 3.45, currency="CAD")],
            "losers": [_make_quote("BCE.TO", "BCE Inc.", 44.10, -1.80, currency="CAD")],
        }
        fake_us_movers = {
            "gainers": [_make_quote("NVDA", "NVIDIA Corp.", 138.20, 4.10)],
            "losers": [_make_quote("INTC", "Intel Corp.", 22.15, -2.50)],
            "actives": [_make_quote("NVDA", "NVIDIA Corp.", 138.20, 4.10)],
        }

        with (
            patch("src.skills.markets.markets._yahoo_agg.fetch_quotes", return_value=fake_quotes),
            patch("src.skills.markets.markets._yahoo_agg.fetch_tsx_movers", return_value=fake_tsx_movers),
            patch("src.skills.markets.markets._yahoo_agg.fetch_us_movers", return_value=fake_us_movers),
            patch(
                "src.skills.markets.markets._yahoo_agg.fetch_macro_market_headlines",
                return_value=["**Global Stocks Climb as Yields Ease** — *Reuters*"],
            ),
            patch("src.skills.markets.markets._crypto_agg.fetch_top_coins", return_value=fake_crypto),
        ):
            overview = skill_markets_info(query="how are global markets doing today", view="overview")
            self.assertIn("GLOBAL MARKETS, COMMODITIES & CRYPTO INTELLIGENCE", overview)
            self.assertIn("S&P/TSX Composite", overview)
            self.assertIn("S&P 500", overview)
            self.assertIn("Gold", overview)
            self.assertIn("Bitcoin (`BTC`)", overview)
            self.assertIn("S&P/TSX 60 Top Gainers", overview)
            self.assertIn("US Market Top Gainers", overview)

            pulse = get_daily_market_pulse()
            self.assertIn("DAILY GLOBAL MARKET PULSE & MACRO SIGNALS", pulse)
            self.assertIn("S&P/TSX Composite", pulse)
            self.assertIn("S&P 500", pulse)
            self.assertIn("Gold", pulse)
            self.assertIn("Bitcoin (BTC)", pulse)

    def test_candidate_ticker_extraction_and_tool_arg_aliases(self):
        """Verify _extract_candidate_ticker_query distinguishes broad market queries from single-ticker lookups."""
        self.assertIsNone(_extract_candidate_ticker_query("how are global markets doing today", ""))
        self.assertIsNone(_extract_candidate_ticker_query("what stocks are going up and down today", ""))
        self.assertEqual(_extract_candidate_ticker_query("how is shopify stock doing today", ""), "shopify")
        self.assertEqual(_extract_candidate_ticker_query("", "NVDA"), "NVDA")

        aliases = TOOL_ARG_ALIASES["skill_markets_info"]
        self.assertEqual(aliases["symbol"], "ticker")
        self.assertEqual(aliases["country"], "region")
        self.assertEqual(aliases["mode"], "view")


if __name__ == "__main__":
    unittest.main()
