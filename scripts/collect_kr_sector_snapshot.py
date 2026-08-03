#!/usr/bin/env python3
"""네이버 증권 업종별 시세 페이지를 스크래핑해 data/kr_sector_history.json에
국내(코스피+코스닥 통합 WICS 분류) 업종 스냅샷을 추가한다.

collect_sector_snapshot.py(미국, Finviz)와 같은 보존 정책(same-day 가드, 빈
응답 가드, 최근 60개 롤링)을 쓰지만 데이터 소스와 지표가 달라 별도 스크립트로
둔다. 네이버 업종별 시세는 시가총액을 안 주기 때문에(미국 쪽 Finviz는 줌)
트리맵 크기는 시가총액 대신 업종 소속 종목 수(total)로 근사한다.

사용 예:
    python scripts/collect_kr_sector_snapshot.py --label market_open
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

_URL = "https://finance.naver.com/sise/sise_group.naver"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "kr_sector_history.json"
_MAX_HISTORY = 60  # 하루 2회 기준 약 30일치


def _get_with_retry(url: str, params: dict, max_retries: int = 3, backoff: float = 5.0) -> requests.Response:
    for attempt in range(max_retries + 1):
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        if attempt == max_retries:
            resp.raise_for_status()
        wait = backoff * (2**attempt)
        print(f"네이버 429 응답, {wait:.0f}초 후 재시도 ({attempt + 1}/{max_retries})")
        time.sleep(wait)
    raise RuntimeError("unreachable")


def fetch_kr_sector_snapshot() -> list[dict]:
    """전 업종(코스피+코스닥 통합, 네이버 WICS 분류)의 당일 등락률 스냅샷. 등락률 내림차순."""
    resp = _get_with_retry(_URL, params={"type": "upjong"})
    resp.encoding = "euc-kr"

    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.select_one("table.type_1")
    if table is None:
        raise RuntimeError("네이버 업종별 시세 테이블을 찾지 못했습니다 (페이지 구조 변경 가능성)")

    records = []
    for tr in table.select("tr"):
        link = tr.select_one("td a")
        cells = tr.select("td.number")
        if link is None or len(cells) < 5:
            continue
        name = link.get_text(strip=True)
        href = link.get("href", "")
        group_no = href.split("no=")[-1] if "no=" in href else None
        try:
            change_pct = float(cells[0].get_text(strip=True).replace("%", ""))
            total, up, flat, down = (int(c.get_text(strip=True)) for c in cells[1:5])
        except ValueError:
            continue
        records.append(
            {
                "name": name,
                "change_pct": change_pct,
                "total": total,
                "up": up,
                "flat": flat,
                "down": down,
                "up_ratio_pct": round(up / total * 100, 1) if total else 0.0,
                "group_no": group_no,
            }
        )

    if not records:
        raise RuntimeError("네이버 업종별 시세 데이터를 하나도 파싱하지 못했습니다.")

    records.sort(key=lambda r: r["change_pct"], reverse=True)
    return records


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
    parser = argparse.ArgumentParser(description="네이버 국내 업종 스냅샷 수집")
    parser.add_argument(
        "--label", default="manual", help="이 스냅샷을 구분할 라벨 (market_open / midnight_kst / manual)"
    )
    args = parser.parse_args()

    history = load_history()
    if already_collected_today(history, args.label):
        print(f"오늘 이미 label={args.label}로 수집됨 — 버퍼용 재실행이라 건너뜁니다.")
        return

    try:
        industries = fetch_kr_sector_snapshot()
    except Exception as e:  # noqa: BLE001 - 실패해도 기존 히스토리는 보존
        print(f"스크래핑 실패, 기존 히스토리는 그대로 둡니다: {e}")
        return

    if not industries:
        print("파싱된 업종이 0개라 히스토리를 갱신하지 않습니다.")
        return

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
