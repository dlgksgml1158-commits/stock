#!/usr/bin/env python3
"""Finviz 스크리너에서 "거래량은 몰렸는데 아직 가격은 안 튄" 종목을 찾아
data/presurge_history.json에 스냅샷을 추가한다.

신호: 상대거래량(오늘 거래량 / 평소 평균거래량)이 평소보다 뚜렷하게 높은데
(sh_relvol_o2 = 2배 이상), 당일 등락률은 아직 완만한(-3% ~ +6%) 종목.
이미 크게 튄 종목(뉴스로 급등/급락 끝난 것)은 relvol이 극단적으로 높게 나와서
그냥 relvol 내림차순 정렬만 하면 다 이런 것들이 상위를 차지해버리므로,
등락률 밴드로 걸러야 "매수세는 몰리는데 아직 조용한" 종목만 남는다.
레버리지/인버스 ETF도 relvol이 잘 튀는데 이건 신호가 아니라 상품 구조상
노이즈라 ind_stocksonly로 원천 제외한다.

사용 예:
    python scripts/collect_presurge_screener.py --label market_open
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
_HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "presurge_history.json"
_MAX_HISTORY = 60  # 하루 2회 기준 약 30일치
_MAX_TOP_STORED = 30  # 스냅샷당 저장할 후보 수 (전체 매칭이 아니라 relvol 상위만)

_FILTERS = "sh_relvol_o2,sh_avgvol_o500,sh_price_o5,ind_stocksonly"
_COLUMNS = "0,1,2,3,6,65,66,63,64,67,68"  # No,Ticker,Company,Sector,MktCap,Price,Change,AvgVol,RelVol,Volume,Earnings
_MAX_PAGES = 5  # 페이지당 20개, relvol 상위 100개면 충분
_CHANGE_MIN, _CHANGE_MAX = -3.0, 6.0


def _get_with_retry(url: str, params: dict, max_retries: int = 4, backoff: float = 5.0) -> requests.Response:
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


def fetch_presurge_candidates() -> list[dict]:
    """relvol 2배+, 등락률 -3~+6%, ETF 제외. relvol 내림차순, 상위 _MAX_TOP_STORED개."""
    candidates = []
    for page_start in range(1, _MAX_PAGES * 20, 20):
        resp = _get_with_retry(
            _SCREENER_URL, params={"v": "152", "f": _FILTERS, "c": _COLUMNS, "r": page_start}
        )
        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", class_="screener_table")
        if table is None:
            break  # 마지막 페이지 이후 (결과 소진)

        rows = table.find_all("tr")[1:]  # 첫 행은 헤더
        if not rows:
            break

        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 11:
                continue
            vals = [td.get_text(strip=True) for td in tds]
            # vals[0]은 Finviz가 항상 자동으로 붙이는 "No." 순번 컬럼.
            _, ticker, company, sector, market_cap, price, change, avg_vol, rel_vol, volume, earnings = vals
            try:
                change_pct = float(change.replace("%", ""))
                relvol = float(rel_vol)
                price_f = float(price)
            except ValueError:
                continue
            candidates.append(
                {
                    "ticker": ticker,
                    "company": company,
                    "sector": sector,
                    "market_cap": market_cap,
                    "price": price_f,
                    "change_pct": change_pct,
                    "avg_volume": avg_vol,
                    "rel_volume": relvol,
                    "volume": volume,
                    "earnings": earnings,
                }
            )

        if len(rows) < 20:
            break  # 마지막 페이지

        time.sleep(1.5)  # 페이지 사이 딜레이 (429 예방)

    presurge = [c for c in candidates if _CHANGE_MIN <= c["change_pct"] <= _CHANGE_MAX]
    presurge.sort(key=lambda c: c["rel_volume"], reverse=True)
    return presurge[:_MAX_TOP_STORED]


def already_collected_today(history: list[dict], label: str) -> bool:
    """sector-dashboard의 collect_sector_snapshot.py와 동일한 same-day 가드.

    버퍼용 cron이 같은 라벨로 여러 번 걸려도, 정시 실행이 이미 성공했으면
    나머지는 건너뛴다. manual은 항상 예외.
    """
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
    parser = argparse.ArgumentParser(description="Finviz 급등 전 후보(relvol 몰림 + 완만한 등락률) 수집")
    parser.add_argument(
        "--label", default="manual", help="이 스냅샷을 구분할 라벨 (market_open / midnight_kst / manual)"
    )
    args = parser.parse_args()

    history = load_history()
    if already_collected_today(history, args.label):
        print(f"오늘 이미 label={args.label}로 수집됨 — 버퍼용 재실행이라 건너뜁니다.")
        return

    try:
        candidates = fetch_presurge_candidates()
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
