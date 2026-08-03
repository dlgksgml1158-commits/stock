#!/usr/bin/env python3
"""Finviz 스크리너에서 "오늘 급등할 가능성이 있고, 급등하면 숏스퀴즈로 이어질 수
있는" 종목을 찾아 data/shortsqueeze_history.json에 스냅샷을 추가한다.

세 조건을 모두 만족해야 한다:
1. 스퀴즈 구조: 공매도비중(Short Float) 20% 이상 + 커버일수(Short Ratio, 공매도
   잔량을 평소 거래량으로 다 청산하는 데 걸리는 날짜 수)가 길면, 공매도 세력이
   주가 상승 시 빠르게 못 빠져나가서 강제 숏커버링(매수)이 매수를 더 부르는
   되먹임이 걸릴 수 있다.
2. 오늘 실제로 움직이는 중: 상대거래량(relvol, 오늘 거래량/평소 평균거래량)
   1.5배 이상. Short Float/Short Ratio는 거래소가 2주에 한 번 집계하는 정적
   지표라 이것만 보면 "오늘 아무 일도 없는" 종목이 계속 1등으로 나온다(실제로
   확인해보니 당일 거래량이 평소보다 오히려 적은 종목이 상위였음).
3. 아직 안 터짐: 당일 등락률이 완만한(-3%~+6%) 종목만. relvol이 높아도 이미
   +107%, -27%처럼 크게 움직인 종목은 "오늘 급등 가능성이 있는" 게 아니라
   "오늘 이미 터진" 종목이라 제외한다 — collect_presurge_screener.py와 같은
   기준의 밴드.

스퀴즈 점수 = 공매도비중 x 커버일수 x 상대거래량. 레버리지/인버스 ETF는 이 개념
자체가 안 맞으므로 ind_stocksonly로 제외.

사용 예:
    python scripts/collect_shortsqueeze_screener.py --label market_open
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

_SCREENER_URL = "https://finviz.com/screener.ashx"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "shortsqueeze_history.json"
_MAX_HISTORY = 60  # 하루 2회 기준 약 30일치
_MAX_TOP_STORED = 30

_FILTERS = "sh_short_o20,sh_relvol_o1.5,sh_avgvol_o200,sh_price_o2,ind_stocksonly"
_MAX_PAGES = 5  # 페이지당 20개 (오늘 relvol 조건까지 걸려서 전체 매칭 수 자체가 적음)
_CHANGE_MIN, _CHANGE_MAX = -3.0, 6.0  # 이 밖이면 "아직 안 터짐"이 아니라 "이미 터짐"


def _get_with_retry(url: str, params: dict, max_retries: int = 5, backoff: float = 8.0) -> requests.Response:
    """429(Too Many Requests)를 지수 백오프로 재시도."""
    for attempt in range(max_retries + 1):
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        if attempt == max_retries:
            resp.raise_for_status()
        wait = backoff * (2**attempt)
        print(f"Finviz 429 응답, {wait:.0f}초 후 재시도 ({attempt + 1}/{max_retries})")
        time.sleep(wait)
    raise RuntimeError("unreachable")


def _parse_size(text: str) -> float | None:
    text = text.strip()
    if text in ("-", ""):
        return None
    mult = 1.0
    if text.endswith("B"):
        mult, text = 1e9, text[:-1]
    elif text.endswith("M"):
        mult, text = 1e6, text[:-1]
    elif text.endswith("K"):
        mult, text = 1e3, text[:-1]
    return float(text) * mult


def fetch_squeeze_candidates() -> list[dict]:
    """공매도비중≥20% + 오늘 relvol≥1.5(서버 필터) + 등락률 -3~+6%(아직 안 터짐,
    로컬 필터)인 종목을, 스퀴즈 점수(공매도비중 x 커버일수 x 오늘 relvol)
    내림차순으로 정렬해 상위만 반환."""
    candidates = []
    for page_start in range(1, _MAX_PAGES * 20, 20):
        resp = _get_with_retry(
            _SCREENER_URL,
            params={"v": "131", "f": _FILTERS, "o": "-shortinterestshare", "r": page_start},
        )
        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", class_="screener_table")
        if table is None:
            break

        rows = table.find_all("tr")[1:]  # 첫 행은 헤더
        if not rows:
            break

        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 15:
                continue
            ticker_cell = tds[1]
            ticker = ticker_cell.get("data-boxover-ticker")
            company = ticker_cell.get("data-boxover-company", "")
            vals = [td.get_text(strip=True) for td in tds]
            # 인덱스: 0=No,1=Ticker,2=MktCap,3=Outstanding,4=Float,5=InsiderOwn,6=InsiderTrans,
            #        7=InstOwn,8=InstTrans,9=ShortFloat,10=ShortRatio,11=AvgVolume,12=Price,13=Change,14=Volume
            if not ticker:
                continue
            try:
                float_shares = _parse_size(vals[4])
                short_float_pct = float(vals[9].replace("%", ""))
                short_ratio = float(vals[10])
                avg_volume = _parse_size(vals[11])
                price = float(vals[12])
                change_pct = float(vals[13].replace("%", ""))
                volume = float(vals[14].replace(",", ""))
            except ValueError:
                continue
            if not avg_volume:
                continue  # relvol을 못 구하면 "오늘" 조건을 판단할 수 없어 제외
            rel_volume = round(volume / avg_volume, 2)
            candidates.append(
                {
                    "ticker": ticker,
                    "company": company,
                    "float_shares": float_shares,
                    "short_float_pct": short_float_pct,
                    "short_ratio_days": short_ratio,
                    "rel_volume": rel_volume,
                    "squeeze_score": round(short_float_pct * short_ratio * rel_volume, 1),
                    "avg_volume": avg_volume,
                    "price": price,
                    "change_pct": change_pct,
                    "volume": volume,
                }
            )

        if len(rows) < 20:
            break
        time.sleep(1.5)  # 페이지 사이 딜레이 (429 예방)

    not_yet_popped = [c for c in candidates if _CHANGE_MIN <= c["change_pct"] <= _CHANGE_MAX]
    not_yet_popped.sort(key=lambda c: c["squeeze_score"], reverse=True)
    return not_yet_popped[:_MAX_TOP_STORED]


def already_collected_today(history: list[dict], label: str) -> bool:
    """sector-dashboard의 다른 수집 스크립트와 동일한 same-day 가드. manual은 예외."""
    if label == "manual":
        return False
    today_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).date()
    for entry in history:
        if entry.get("label") != label:
            continue
        entry_kst = (datetime.fromisoformat(entry["timestamp"]) + timedelta(hours=9)).date()
        if entry_kst == today_kst:
            return True
    return False


def load_history() -> list[dict]:
    if not _HISTORY_PATH.exists():
        return []
    try:
        return json.loads(_HISTORY_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def save_history(history: list[dict]) -> None:
    _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2, allow_nan=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Finviz 급등 전 숏스퀴즈 후보(공매도비중 x 커버일수 x relvol, 아직 안 터진 것만) 수집")
    parser.add_argument(
        "--label", default="manual", help="이 스냅샷을 구분할 라벨 (market_open / midnight_kst / manual)"
    )
    args = parser.parse_args()

    history = load_history()
    if already_collected_today(history, args.label):
        print(f"오늘 이미 label={args.label}로 수집됨 — 버퍼용 재실행이라 건너뜁니다.")
        return

    try:
        candidates = fetch_squeeze_candidates()
    except Exception as e:  # noqa: BLE001 - 실패해도 기존 히스토리는 보존
        print(f"스크래핑 실패, 기존 히스토리는 그대로 둡니다: {e}")
        return

    if not candidates:
        print("조건에 맞는 후보가 0개라 히스토리를 갱신하지 않습니다.")
        return

    history.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "label": args.label,
            "candidates": candidates,
        }
    )
    history = history[-_MAX_HISTORY:]
    save_history(history)
    print(f"{len(candidates)}개 후보 저장 완료 (label={args.label}, 히스토리 {len(history)}개 항목)")


if __name__ == "__main__":
    main()
