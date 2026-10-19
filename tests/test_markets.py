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
    _crypto_agg,
    _extract_candidate_ticker_query,
    _extract_timeframe,
    _yahoo_agg,
)
from src.skills.markets.models import CryptoAssetQuote, HistoricalSummary, TickerQuote
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
        fake_hist = HistoricalSummary(
            symbol="SHOP.TO",
            currency="CAD",
            timeframe_label="6-Month (6M)",
            start_date="Apr 18, 2026",
            end_date="Oct 18, 2026",
            start_price=112.00,
            end_price=148.50,
            abs_change=36.50,
            pct_change=32.59,
            period_high=155.20,
            period_high_date="Sep 24, 2026",
            period_low=108.40,
            period_low_date="May 02, 2026",
            max_drawdown_pct=-9.45,
            annualized_volatility_pct=34.2,
            horizon_returns={"1W": 4.2, "1M": 9.8, "3M": 18.5, "6M": 32.59, "YTD": 27.4, "1Y": 54.1, "5Y": 88.0},
            checkpoints=["Apr 18: **112.00**", "Jul 18: **131.40**", "Oct 18: **148.50**"],
        )

        with (
            patch(
                "src.skills.markets.markets._yahoo_agg.resolve_ticker_and_news",
                return_value=("SHOP.TO", "SHOP", fake_news),
            ),
            patch(
                "src.skills.markets.markets._yahoo_agg.fetch_quotes",
                return_value={"SHOP.TO": shop_to, "SHOP": shop_us},
            ),
            patch(
                "src.skills.markets.markets._yahoo_agg.fetch_historical_summary",
                return_value=fake_hist,
            ),
        ):
            output = skill_markets_info(ticker="Shopify", timeframe="6m")

        self.assertIn("TICKER INTELLIGENCE SNAPSHOT: Shopify Inc. (SHOP.TO)", output)
        self.assertIn("148.50 CAD", output)
        self.assertIn("Dual-Listed Quote (`SHOP` — NYQ):", output)
        self.assertIn("107.60 USD", output)
        self.assertIn("Multi-Horizon Returns:", output)
        self.assertIn("**6M:** +32.59%", output)
        self.assertIn("HISTORICAL PERFORMANCE & TREND: 6-Month (6M)", output)
        self.assertIn("112.00 CAD → 148.50 CAD", output)
        self.assertIn("Max Drawdown:", output)
        self.assertIn("Chronological Price Trajectory:", output)
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
        self.assertEqual(aliases["period"], "timeframe")

    def test_timeframe_extraction_and_historical_summary_computation(self):
        """Verify _extract_timeframe and fetch_historical_summary compute returns, drawdowns, and checkpoints."""
        self.assertEqual(_extract_timeframe("how has gold performed over the last 6 months", ""), "6mo")
        self.assertEqual(_extract_timeframe("Shopify 5 year history", ""), "5y")
        self.assertEqual(_extract_timeframe("TSX YTD performance", ""), "ytd")
        self.assertEqual(_extract_timeframe("how did NVDA do in 2024", ""), "2024")
        self.assertEqual(_extract_timeframe("", "3m"), "3mo")

        # Build synthetic 400-day daily series
        base_ts = 1750000000 - (400 * 86400)
        synthetic_bars = []
        for d in range(400):
            ts = base_ts + d * 86400
            price = 100.0 + (d * 0.25)  # climbs from 100.00 to 199.75
            synthetic_bars.append((ts, price - 0.5, price + 1.0, price - 1.0, price))

        with patch.object(_yahoo_agg, "_fetch_chart_bars", return_value=("CAD", synthetic_bars)):
            hist = _yahoo_agg.fetch_historical_summary("SHOP.TO", timeframe="6mo")

        self.assertIsNotNone(hist)
        self.assertEqual(hist.symbol, "SHOP.TO")
        self.assertEqual(hist.currency, "CAD")
        self.assertEqual(hist.timeframe_label, "6-Month (6M)")
        self.assertGreater(hist.pct_change, 20.0)
        self.assertIn("1W", hist.horizon_returns)
        self.assertIn("1M", hist.horizon_returns)
        self.assertIn("6M", hist.horizon_returns)
        self.assertIn("1Y", hist.horizon_returns)
        self.assertEqual(len(hist.checkpoints), 6)

    def test_yahoo_v7_to_v8_chart_fallback_preserves_all_symbols_and_technicals(self):
        """When Yahoo v7/finance/quote fails (e.g. 429/500), v8/finance/chart fallback must recover all symbols and reconstruct MAs/volume."""
        class _DummyResp:
            def __init__(self, status_code: int, payload: dict):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class _DummySession:
            def get(self, url: str, timeout: int = 5):
                if "/v7/finance/quote" in url:
                    return _DummyResp(429, {})
                if "/v8/finance/chart/" in url:
                    # Extract symbol from URL path
                    sym = url.split("/v8/finance/chart/")[1].split("?")[0].replace("%5E", "^").replace("%3D", "=")
                    closes = [100.0 + i * 0.5 for i in range(220)]  # ends at 209.5, prev_close = 209.0
                    vols = [1_000_000 + (i * 1000) for i in range(220)]
                    return _DummyResp(
                        200,
                        {
                            "chart": {
                                "result": [
                                    {
                                        "meta": {
                                            "symbol": sym,
                                            "longName": f"{sym} Asset",
                                            "fullExchangeName": "TOR" if sym.endswith(".TO") else "NMS",
                                            "currency": "CAD" if sym.endswith(".TO") else "USD",
                                            "regularMarketPrice": 210.0,
                                            "regularMarketDayLow": 208.0,
                                            "regularMarketDayHigh": 211.5,
                                            "fiftyTwoWeekLow": 95.0,
                                            "fiftyTwoWeekHigh": 215.0,
                                            "regularMarketVolume": 1_500_000,
                                        },
                                        "indicators": {
                                            "quote": [{"close": closes, "volume": vols}]
                                        },
                                    }
                                ]
                            }
                        },
                    )
                return _DummyResp(404, {})

        symbols = [f"SYM{i}" for i in range(12)] + ["SHOP.TO"]
        with patch(
            "src.skills.markets.aggregators.yahoo._get_session_and_crumb",
            return_value=(_DummySession(), "testcrumb"),
        ):
            fetched = _yahoo_agg.fetch_quotes(symbols)

        # Verify zero symbols were dropped (even >8 symbols) and all technicals were reconstructed
        self.assertEqual(len(fetched), 13)
        shop_q = fetched["SHOP.TO"]
        self.assertEqual(shop_q.currency, "CAD")
        self.assertEqual(shop_q.price, 210.0)
        # 1-day change vs closes[-2] (209.0) -> +1.0 (+0.48%)
        self.assertAlmostEqual(shop_q.change, 1.0, places=2)
        self.assertIsNotNone(shop_q.ma50)
        self.assertIsNotNone(shop_q.ma200)
        self.assertIsNotNone(shop_q.avg_volume_3m)
        self.assertEqual(shop_q.week52_low, 95.0)
        self.assertEqual(shop_q.week52_high, 215.0)

    def test_yahoo_crumb_401_auto_refresh_recovery(self):
        """When Yahoo v7 returns 401/403 due to an expired crumb, fetch_quotes refreshes the crumb and succeeds on retry."""
        calls = []

        class _DummyResp:
            def __init__(self, status_code: int, payload: dict):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class _DummySession:
            def get(self, url: str, timeout: int = 6):
                calls.append(url)
                if len(calls) == 1:
                    return _DummyResp(401, {})
                return _DummyResp(
                    200,
                    {
                        "quoteResponse": {
                            "result": [
                                {
                                    "symbol": "GC=F",
                                    "longName": "Gold Futures",
                                    "currency": "USD",
                                    "regularMarketPrice": 4195.0,
                                    "regularMarketChange": 15.0,
                                    "regularMarketChangePercent": 0.36,
                                }
                            ]
                        }
                    },
                )

        sess = _DummySession()
        with patch(
            "src.skills.markets.aggregators.yahoo._get_session_and_crumb",
            side_effect=[(sess, "stale_crumb"), (sess, "fresh_crumb")],
        ):
            res = _yahoo_agg.fetch_quotes(["GC=F"])

        self.assertEqual(len(calls), 2)
        self.assertIn("GC=F", res)
        self.assertEqual(res["GC=F"].price, 4195.0)

    def test_coingecko_rate_limit_fallback_to_yahoo_preserves_crypto_and_movers(self):
        """When CoinGecko returns 429 or times out, CryptoAggregator falls back to Yahoo Finance with full USD/CAD & movers."""
        class _DummyResp429:
            status_code = 429

        fake_yf_quotes = {
            "BTC-USD": _make_quote("BTC-USD", "Bitcoin", 82500.0, 1.85),
            "ETH-USD": _make_quote("ETH-USD", "Ethereum", 2420.0, 0.95),
            "SOL-USD": _make_quote("SOL-USD", "Solana", 105.0, 3.40),
            "XRP-USD": _make_quote("XRP-USD", "XRP", 1.31, -1.20),
            "DOGE-USD": _make_quote("DOGE-USD", "Dogecoin", 0.085, -2.80),
            "ZEC-USD": _make_quote("ZEC-USD", "Zcash", 1165.0, 4.50),
        }

        with (
            patch("src.skills.markets.aggregators.crypto.creq.get", return_value=_DummyResp429()),
            patch("src.skills.markets.aggregators.yahoo.YahooFinanceAggregator.fetch_quotes", return_value=fake_yf_quotes),
        ):
            coins = _crypto_agg.fetch_top_coins(usdcad_rate=1.40)

        self.assertGreaterEqual(len(coins), 6)
        btc = next(c for c in coins if c.symbol == "BTC")
        self.assertEqual(btc.price_usd, 82500.0)
        self.assertEqual(btc.price_cad, round(82500.0 * 1.40, 2))
        self.assertIsNotNone(btc.market_cap_usd)
        self.assertIsNotNone(btc.volume_24h_usd)

        movers = _crypto_agg.get_crypto_movers(coins, count=2)
        self.assertEqual(movers["gainers"][0].symbol, "ZEC")
        self.assertEqual(movers["losers"][0].symbol, "DOGE")


if __name__ == "__main__":
    unittest.main()


