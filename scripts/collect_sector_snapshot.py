#!/usr/bin/env python3
"""Finviz 업종 성과 페이지를 스크래핑해 data/sector_history.json에 스냅샷을 추가한다.

GitHub Actions ubuntu 러너에서 단독으로 실행되므로(다른 로컬 프로젝트/venv에
의존하지 않음) stdlib + requests/bs4/lxml만 사용한다. 스크래핑 로직 자체는
~/stock-orchestra/data/us_sector_data.py에서 이미 검증한 것을 그대로 이식했다.

사용 예:
    python scripts/collect_sector_snapshot.py --label market_open
    python scripts/collect_sector_snapshot.py --label midnight_kst
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

_GROUPS_URL = "https://finviz.com/groups.ashx"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "sector_history.json"
_MAX_HISTORY = 60  # 하루 2회 기준 약 30일치


def _parse_size(text: str) -> float | None:
    text = text.strip()
    if text in ("-", ""):
        return None  # JSON has no NaN literal; None -> JSON null
    mult = 1.0
    if text.endswith("B"):
        mult, text = 1e9, text[:-1]
    elif text.endswith("M"):
        mult, text = 1e6, text[:-1]
    elif text.endswith("K"):
        mult, text = 1e3, text[:-1]
    return float(text) * mult


def _parse_float_or_nan(text: str) -> float | None:
    text = text.strip()
    if text in ("-", ""):
        return None  # JSON has no NaN literal; None -> JSON null
    return float(text)


def _parse_pct(text: str) -> float:
    return float(text.strip().replace("%", ""))


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


def fetch_industry_snapshot() -> list[dict]:
    """미국 주식 업종(~140개) 당일 등락률 스냅샷. 등락률 내림차순."""
    resp = _get_with_retry(_GROUPS_URL, params={"g": "industry", "v": "152"})
    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.find("table", class_="groups_table")
    if table is None:
        raise RuntimeError("Finviz 업종 테이블을 찾지 못했습니다 (페이지 구조 변경 가능성)")

    records = []
    for tr in table.find_all("tr"):
        link = tr.find("a", class_="tab-link")
        tds = tr.find_all("td")
        if link is None or len(tds) < 9:
            continue
        href = link.get("href", "")
        filter_key = href.split("f=")[-1].split("&")[0] if "f=" in href else None
        name = link.get_text(strip=True)
        try:
            market_cap = _parse_size(tds[2].get_text(strip=True))
            pe = _parse_float_or_nan(tds[3].get_text(strip=True))
            dividend_pct = _parse_pct(tds[4].get_text(strip=True))
            avg_volume = _parse_size(tds[5].get_text(strip=True))
            change_pct = _parse_pct(tds[6].get_text(strip=True))
            volume = _parse_size(tds[7].get_text(strip=True))
            stocks = int(tds[8].get_text(strip=True))
        except ValueError:
            continue
        records.append(
            {
                "name": name,
                "change_pct": change_pct,
                "market_cap": market_cap,
                "pe": pe,
                "dividend_pct": dividend_pct,
                "avg_volume": avg_volume,
                "volume": volume,
                "volume_ratio": round(volume / avg_volume, 2) if avg_volume else None,
                "stocks": stocks,
                "filter_key": filter_key,
            }
        )

    records.sort(key=lambda r: r["change_pct"], reverse=True)
    return records


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
    parser = argparse.ArgumentParser(description="Finviz 업종 스냅샷 수집")
    parser.add_argument(
        "--label", default="manual", help="이 스냅샷을 구분할 라벨 (market_open / midnight_kst / manual)"
    )
    args = parser.parse_args()

    try:
        industries = fetch_industry_snapshot()
    except Exception as e:  # noqa: BLE001 - 실패해도 기존 히스토리는 보존
        print(f"스크래핑 실패, 기존 히스토리는 그대로 둡니다: {e}")
        return

    if not industries:
        # 빈 응답으로 기존 히스토리를 덮어쓰지 않는다 (일시적 스크래핑 실패 대비 가드).
        print("파싱된 업종이 0개라 히스토리를 갱신하지 않습니다.")
        return

    history = load_history()
    history.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "label": args.label,
            "industries": industries,
        }
    )
    history = history[-_MAX_HISTORY:]
    save_history(history)
    print(f"{len(industries)}개 업종 저장 완료 (label={args.label}, 히스토리 {len(history)}개 항목)")


if __name__ == "__main__":
    main()
