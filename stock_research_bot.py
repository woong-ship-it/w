#!/usr/bin/env python3
"""
Korean stock research/study bot.

Features
1) Download and read research materials (URL/PDF/HTML/TXT)
2) Fetch Naver Finance chart API data
3) Read disclosures and business reports via DART Open API
4) Generate 3 institutional-style notes:
   - Goldman Sachs fundamental screener
   - Morgan Stanley technical dashboard
   - JPMorgan earnings analyzer
"""

from __future__ import annotations

import argparse
import io
import json
import os
import textwrap
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from dateutil import parser as dt_parser
from lxml import etree
from pypdf import PdfReader


@dataclass
class TradingPlan:
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward_1: float
    risk_reward_2: float


class StockResearchBot:
    def __init__(self, dart_api_key: Optional[str] = None, timeout: int = 20):
        self.timeout = timeout
        self.dart_api_key = dart_api_key or os.getenv("DART_API_KEY")

    # -----------------------------
    # 1) Research material download/read
    # -----------------------------
    def download_and_read_material(self, url: str, save_path: Optional[str] = None) -> Dict[str, Any]:
        """Download a material (pdf/html/txt/json) and return parsed text preview."""
        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "")
        raw = r.content

        if save_path:
            with open(save_path, "wb") as f:
                f.write(raw)

        text = ""
        if "pdf" in content_type.lower() or url.lower().endswith(".pdf"):
            reader = PdfReader(io.BytesIO(raw))
            pages = [p.extract_text() or "" for p in reader.pages]
            text = "\n".join(pages)
        elif "json" in content_type.lower() or url.lower().endswith(".json"):
            text = json.dumps(r.json(), ensure_ascii=False, indent=2)
        else:
            # html/txt fallback
            text = r.text

        preview = text[:4000]
        return {
            "url": url,
            "content_type": content_type,
            "bytes": len(raw),
            "preview": preview,
        }

    # -----------------------------
    # 2) Naver chart API
    # -----------------------------
    def fetch_naver_chart(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        timeframe: str = "day",
    ) -> pd.DataFrame:
        """Fetch OHLCV from Naver chart API."""
        # Unofficial endpoint returning JS-like array string.
        url = (
            "https://api.finance.naver.com/siseJson.naver"
            f"?symbol={symbol}&requestType=1&startTime={start_date}"
            f"&endTime={end_date}&timeframe={timeframe}"
        )
        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()

        txt = r.text.strip()
        # Naver returns single-quoted JS array; normalize to JSON.
        normalized = txt.replace("'", '"')
        data = json.loads(normalized)

        if len(data) <= 1:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

        header = [h.lower() for h in data[0]]
        rows = data[1:]
        df = pd.DataFrame(rows, columns=header)
        # standardize column names
        rename_map = {
            "날짜": "date",
            "date": "date",
            "시가": "open",
            "open": "open",
            "고가": "high",
            "high": "high",
            "저가": "low",
            "low": "low",
            "종가": "close",
            "close": "close",
            "거래량": "volume",
            "volume": "volume",
        }
        df = df.rename(columns=rename_map)

        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
        return df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)

    # -----------------------------
    # 3) DART Open API
    # -----------------------------
    def _require_dart_key(self):
        if not self.dart_api_key:
            raise ValueError("DART_API_KEY is required. Set env var or pass dart_api_key.")

    def get_dart_corp_code(self, corp_name: str) -> Optional[str]:
        """Get corp_code by corp_name using DART corpCode.xml."""
        self._require_dart_key()
        url = "https://opendart.fss.or.kr/api/corpCode.xml"
        r = requests.get(url, params={"crtfc_key": self.dart_api_key}, timeout=self.timeout)
        r.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            xml_name = zf.namelist()[0]
            xml_bytes = zf.read(xml_name)

        root = etree.fromstring(xml_bytes)
        for el in root.findall("list"):
            name = (el.findtext("corp_name") or "").strip()
            if name == corp_name:
                return (el.findtext("corp_code") or "").strip()
        return None

    def list_dart_filings(
        self,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        pblntf_ty: Optional[str] = None,
        page_count: int = 100,
    ) -> List[Dict[str, Any]]:
        self._require_dart_key()
        url = "https://opendart.fss.or.kr/api/list.json"
        params = {
            "crtfc_key": self.dart_api_key,
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_no": 1,
            "page_count": page_count,
            "last_reprt_at": "Y",
        }
        if pblntf_ty:
            params["pblntf_ty"] = pblntf_ty
        r = requests.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        js = r.json()
        if js.get("status") != "000":
            return []
        return js.get("list", [])

    def download_dart_document_xml(self, rcept_no: str) -> str:
        """Download full filing document xml (zipped) by receipt number."""
        self._require_dart_key()
        url = "https://opendart.fss.or.kr/api/document.xml"
        r = requests.get(
            url,
            params={"crtfc_key": self.dart_api_key, "rcept_no": rcept_no},
            timeout=self.timeout,
        )
        r.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            xml_name = zf.namelist()[0]
            xml_text = zf.read(xml_name).decode("utf-8", errors="ignore")
        return xml_text

    # -----------------------------
    # Analytics helpers
    # -----------------------------
    @staticmethod
    def _safe_div(a: float, b: float) -> float:
        return float(a / b) if b not in (0, None) and not pd.isna(b) else np.nan

    def _load_market_data(self, yahoo_ticker: str, period: str = "5y") -> pd.DataFrame:
        df = yf.download(yahoo_ticker, period=period, auto_adjust=False, progress=False)
        if df.empty:
            raise ValueError(f"No market data for {yahoo_ticker}")
        return df

    def _load_fundamentals(self, yahoo_ticker: str) -> Dict[str, Any]:
        t = yf.Ticker(yahoo_ticker)
        info = t.info or {}
        income = t.financials.T if t.financials is not None else pd.DataFrame()
        balance = t.balance_sheet.T if t.balance_sheet is not None else pd.DataFrame()
        cashflow = t.cashflow.T if t.cashflow is not None else pd.DataFrame()
        q_earnings = t.quarterly_earnings if hasattr(t, "quarterly_earnings") else pd.DataFrame()
        return {
            "info": info,
            "income": income,
            "balance": balance,
            "cashflow": cashflow,
            "q_earnings": q_earnings,
        }

    def _calc_technicals(self, px: pd.DataFrame) -> pd.DataFrame:
        df = px.copy()
        close = df["Close"]
        df["MA20"] = close.rolling(20).mean()
        df["MA50"] = close.rolling(50).mean()
        df["MA100"] = close.rolling(100).mean()
        df["MA200"] = close.rolling(200).mean()

        delta = close.diff()
        up = delta.clip(lower=0)
        down = -1 * delta.clip(upper=0)
        roll_up = up.rolling(14).mean()
        roll_down = down.rolling(14).mean()
        rs = roll_up / roll_down
        df["RSI14"] = 100 - (100 / (1 + rs))

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df["MACD"] = ema12 - ema26
        df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()
        df["MACD_HIST"] = df["MACD"] - df["MACD_SIGNAL"]

        std20 = close.rolling(20).std()
        df["BB_MID"] = df["MA20"]
        df["BB_UPPER"] = df["BB_MID"] + 2 * std20
        df["BB_LOWER"] = df["BB_MID"] - 2 * std20

        return df

    def _trade_plan(self, price: float, support: float, resistance: float) -> TradingPlan:
        entry = round((price + support) / 2, 2)
        stop = round(support * 0.97, 2)
        tp1 = round(resistance, 2)
        tp2 = round(resistance * 1.08, 2)
        risk = entry - stop if entry > stop else 1
        rr1 = round((tp1 - entry) / risk, 2)
        rr2 = round((tp2 - entry) / risk, 2)
        return TradingPlan(entry, stop, tp1, tp2, rr1, rr2)

    # -----------------------------
    # Note generators
    # -----------------------------
    def generate_goldman_fundamental_note(self, company_name: str, yahoo_ticker: str) -> str:
        f = self._load_fundamentals(yahoo_ticker)
        info, income, balance, cashflow = f["info"], f["income"], f["balance"], f["cashflow"]

        pe = info.get("trailingPE")
        ps = info.get("priceToSalesTrailing12Months")
        ev_ebitda = info.get("enterpriseToEbitda")

        gross_margin = info.get("grossMargins")
        op_margin = info.get("operatingMargins")
        net_margin = info.get("profitMargins")

        d_to_e = info.get("debtToEquity")
        current_ratio = info.get("currentRatio")
        cash = info.get("totalCash")
        debt = info.get("totalDebt")
        cash_to_debt = self._safe_div(cash or 0, debt or 0)

        fcf = info.get("freeCashflow")
        market_cap = info.get("marketCap")
        fcf_yield = self._safe_div(fcf or 0, market_cap or 0)

        growth_hint = info.get("revenueGrowth")

        bull_target = round((info.get("targetMeanPrice") or info.get("currentPrice", 0)) * 1.15, 2)
        bear_target = round((info.get("targetMeanPrice") or info.get("currentPrice", 0)) * 0.85, 2)

        moat_scores = {
            "가격결정력": 7,
            "브랜드강도": 7,
            "전환비용": 6,
            "네트워크효과": 5,
        }

        rating = "BUY" if (pe and pe < 25 and growth_hint and growth_hint > 0) else "HOLD"
        conviction = "중간-높음" if rating == "BUY" else "중간"

        return textwrap.dedent(
            f"""
            ===============================
            [Goldman Sachs Style Rating Box]
            종목: {company_name} ({yahoo_ticker})
            투자의견: {rating}
            확신수준: {conviction}
            12M 목표가(강세/약세): {bull_target} / {bear_target}
            ===============================

            1) 비즈니스 모델 분석
            - 핵심 사업: {info.get('longBusinessSummary', '요약 데이터 없음')[:240]}...

            2) 수익 흐름
            - 최근 매출 성장 힌트(yfinance revenueGrowth): {growth_hint}
            - 부문별 매출 비중은 DART 사업보고서의 사업부문 주석 병행 확인 권장.

            3) 수익성 분석 (최근 스냅샷)
            - 총마진: {gross_margin}
            - 영업이익률: {op_margin}
            - 순이익률: {net_margin}
            - 5년 추세는 연도별 손익계산서로 후속 계산 필요.

            4) 대차대조표 건전성
            - 부채/자본: {d_to_e}
            - 유동비율: {current_ratio}
            - 현금/총부채: {cash_to_debt:.2f}

            5) 자유현금흐름 분석
            - FCF: {fcf}
            - FCF 수익률(FCF/시총): {fcf_yield:.4f}
            - 자본배분 우선순위: 배당/자사주/Capex/인수합병 공시 추적 필요.

            6) 경쟁 우위(1-10)
            - {moat_scores}

            7) 경영진 품질
            - 내부자 지분, 보상체계 정렬성은 Proxy/DART 임원보수 주석 확인 필요.

            8) 밸류에이션 스냅샷
            - 현재 P/E: {pe}
            - 현재 P/S: {ps}
            - 현재 EV/EBITDA: {ev_ebitda}
            - 5년 평균/섹터 비교는 멀티플 히스토리 DB 병행 권장.

            9) 시나리오
            - 강세: 신제품/마진개선/리레이팅 반영시 {bull_target}
            - 약세: 수요둔화/멀티플디레이팅 반영시 {bear_target}

            10) 결론
            - 당사 의견은 {rating}. 현재 밸류에이션과 성장/수익성 조합은 {conviction} 확신으로 평가.
            """
        ).strip()

    def generate_morgan_technical_note(self, company_name: str, yahoo_ticker: str) -> str:
        px = self._load_market_data(yahoo_ticker, period="2y")
        d = self._calc_technicals(px)
        last = d.iloc[-1]

        close = float(last["Close"])
        high_3m = float(d.tail(63)["High"].max())
        low_3m = float(d.tail(63)["Low"].min())

        support = round(low_3m, 2)
        resistance = round(high_3m, 2)

        rsi = float(last["RSI14"])
        rsi_state = "과매수" if rsi >= 70 else "과매도" if rsi <= 30 else "중립"

        macd = float(last["MACD"])
        macd_signal = float(last["MACD_SIGNAL"])
        macd_view = "상향" if macd > macd_signal else "하향"

        bb_upper = float(last["BB_UPPER"])
        bb_lower = float(last["BB_LOWER"])
        bb_pos = (close - bb_lower) / (bb_upper - bb_lower) if bb_upper > bb_lower else np.nan

        # Fibonacci from latest 6m swing
        swing_high = float(d.tail(126)["High"].max())
        swing_low = float(d.tail(126)["Low"].min())
        diff = swing_high - swing_low
        fib38 = swing_high - diff * 0.382
        fib50 = swing_high - diff * 0.5
        fib62 = swing_high - diff * 0.618

        plan = self._trade_plan(close, support, resistance)

        trend_daily = "상승" if close > last["MA50"] else "하락"
        weekly = d.resample("W").last()
        monthly = d.resample("M").last()
        trend_weekly = "상승" if weekly.iloc[-1]["Close"] > weekly.iloc[-1]["MA20"] else "하락"
        trend_monthly = "상승" if monthly.iloc[-1]["Close"] > monthly.iloc[-1]["MA20"] else "하락"

        return textwrap.dedent(
            f"""
            ======================================
            [Morgan Stanley Trade Plan Summary]
            종목: {company_name} ({yahoo_ticker})
            진입: {plan.entry} | 손절: {plan.stop_loss}
            목표1: {plan.take_profit_1} (R/R {plan.risk_reward_1})
            목표2: {plan.take_profit_2} (R/R {plan.risk_reward_2})
            ======================================

            1) 추세 분석
            - 일간: {trend_daily}
            - 주간: {trend_weekly}
            - 월간: {trend_monthly}

            2) 지지/저항
            - 지지: {support}
            - 저항: {resistance}

            3) 이동평균
            - MA20 {last['MA20']:.2f}, MA50 {last['MA50']:.2f}, MA100 {last['MA100']:.2f}, MA200 {last['MA200']:.2f}

            4) RSI
            - RSI14: {rsi:.2f} ({rsi_state})

            5) MACD
            - MACD {macd:.4f}, Signal {macd_signal:.4f}, 해석: {macd_view} 모멘텀

            6) 볼린저 밴드
            - 상단 {bb_upper:.2f}, 하단 {bb_lower:.2f}, 밴드 내 위치 {bb_pos:.2f}

            7) 거래량
            - 최근 추세 확인은 d.tail(20)[['Close','Volume']] 기반 추가 점검 권장.

            8) 피보나치 (6M 스윙)
            - 38.2%: {fib38:.2f}
            - 50.0%: {fib50:.2f}
            - 61.8%: {fib62:.2f}

            9) 패턴 식별
            - 자동 패턴 인식은 규칙 기반 엔진 추가 가능(현재는 수동 검증 권장).

            10) 거래 설정
            - 엔트리 {plan.entry}, 손절 {plan.stop_loss}, 목표 {plan.take_profit_1}/{plan.take_profit_2}
            """
        ).strip()

    def generate_jpm_earnings_note(self, company_name: str, yahoo_ticker: str) -> str:
        t = yf.Ticker(yahoo_ticker)
        cal = t.calendar
        info = t.info or {}

        q = t.quarterly_income_stmt.T if t.quarterly_income_stmt is not None else pd.DataFrame()
        price = self._load_market_data(yahoo_ticker, period="1y")

        # Approx last 6 quarters EPS actual from yfinance field if present
        earnings_dates = t.earnings_dates if hasattr(t, "earnings_dates") else pd.DataFrame()
        earnings_dates = earnings_dates.head(8) if isinstance(earnings_dates, pd.DataFrame) else pd.DataFrame()

        avg_gap = float((price["High"] - price["Low"]).div(price["Close"]).tail(40).mean())
        implied_move = round(avg_gap * 100, 2)

        next_earnings = "N/A"
        if isinstance(cal, pd.DataFrame) and not cal.empty:
            try:
                next_earnings = str(cal.iloc[0, 0])
            except Exception:
                next_earnings = "N/A"

        consensus_eps = info.get("forwardEps")
        consensus_rev = info.get("revenuePerShare")

        positioning = "관망" if implied_move > 6 else "선별적 선매수"

        return textwrap.dedent(
            f"""
            ======================================
            [JPMorgan Decision Summary + Trading Plan]
            종목: {company_name} ({yahoo_ticker})
            실적 전 포지셔닝: {positioning}
            이벤트 예상 변동성(근사): ±{implied_move}%
            ======================================

            1) 실적 역사 (최근 6개 분기)
            - yfinance earnings_dates / quarterly_income_stmt 기반으로 EPS 비트/미스 테이블 확장 가능.
            - 현재 로드된 실적 이벤트 수: {len(earnings_dates)}

            2) 다가오는 분기 컨센서스
            - EPS 컨센서스(근사): {consensus_eps}
            - 매출 컨센서스 프록시(1주당 매출): {consensus_rev}

            3) 속삭임 숫자
            - 정식 API(IBES/FactSet) 연동 시 정확도 향상. 현재는 미포함.

            4) 주목할 핵심 지표
            - 가이던스(매출 성장률, OPM)
            - FCF/Capex
            - 부문별 마진
            - 재고/수주/AR(업종별)

            5) 부문 기대치
            - 사업부별 추정은 DART 사업보고서 부문매출 + 최근 공시 결합 권장.

            6) 경영진 가이던스 검증
            - 직전 분기 콜에서 제시한 가이던스 vs 실제치 비교 필요.

            7) 옵션 내재변동
            - 옵션체인 연동 시 ATM straddle 기반 이벤트 move 산출 가능.

            8) 과거 실적일 패턴(최근 8회)
            - 평균/중앙 변동치 계산 로직 내장 가능(현재는 일중 변동 프록시 {implied_move}%).

            9) 실적 전 포지셔닝
            - 의견: {positioning}

            10) 실적 후 플레이북
            - 갭업: 첫 30분 고점 돌파 시 추세추종
            - 갭다운: 장초 반등 실패 시 방어적/헤지
            - 플랫: 컨콜 가이던스 발표 후 방향확인

            다음 실적 예정일(제공값): {next_earnings}
            """
        ).strip()


def main():
    p = argparse.ArgumentParser(description="Stock research/study bot")
    p.add_argument("--company", required=True, help="회사명 (예: 삼성전자)")
    p.add_argument("--ticker", required=True, help="Yahoo ticker (예: 005930.KS)")
    p.add_argument("--naver-symbol", help="Naver symbol (예: 005930)")
    p.add_argument("--dart-api-key", default=os.getenv("DART_API_KEY"))
    p.add_argument("--start-date", default=(datetime.utcnow() - timedelta(days=365)).strftime("%Y%m%d"))
    p.add_argument("--end-date", default=datetime.utcnow().strftime("%Y%m%d"))
    args = p.parse_args()

    bot = StockResearchBot(dart_api_key=args.dart_api_key)

    print("\n=== 1) Goldman Fundamental Note ===")
    print(bot.generate_goldman_fundamental_note(args.company, args.ticker))

    print("\n=== 2) Morgan Technical Note ===")
    print(bot.generate_morgan_technical_note(args.company, args.ticker))

    print("\n=== 3) JPM Earnings Note ===")
    print(bot.generate_jpm_earnings_note(args.company, args.ticker))

    if args.naver_symbol:
        print("\n=== Naver Chart API Sample (tail 5) ===")
        chart = bot.fetch_naver_chart(args.naver_symbol, args.start_date, args.end_date)
        print(chart.tail(5).to_string(index=False))

    if args.dart_api_key:
        corp_code = bot.get_dart_corp_code(args.company)
        if corp_code:
            filings = bot.list_dart_filings(corp_code, bgn_de=args.start_date[:4] + "0101", end_de=args.end_date)
            print(f"\n=== DART filings latest (count={len(filings)}) ===")
            for f in filings[:5]:
                print(f"- {f.get('report_nm')} | {f.get('rcept_no')} | {f.get('rcept_dt')}")
        else:
            print(f"\n[DART] corp_code를 찾지 못했습니다: {args.company}")


if __name__ == "__main__":
    main()
