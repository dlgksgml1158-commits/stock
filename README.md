# 주식 대시보드

하루 2번(미국 장 시작 무렵 / 한국시간 자정) 자동 스크래핑해서 보여주는 정적 웹
대시보드. 로그인/API 키 불필요. 페이지 상단 **해외/국내** 토글로 시장을 전환한다.

## 해외(미국, Finviz)

1. **업종 강도** — 업종(~140개) 당일 등락률 기준 강한/약한 업종, 최근 연속으로
   강세인 업종
2. **급등 전 후보** — 상대거래량(오늘 거래량/평소 평균거래량)이 2배 이상으로
   튀었는데 아직 등락률은 완만한(-3%~+6%) 종목. "매수세는 몰리는데 아직 가격은
   안 튄" 종목을 찾는 용도. 이미 크게 움직인 종목/레버리지·인버스 ETF는 제외.
   `~/stock-orchestra/backtest/presurge_signal_study.py`로 S&P500 2년치 검증한
   결과, 신호 후 3~5거래일 구간에서 통계적으로 유의한(작지만 진짜) 초과수익이
   있었음 — 자세한 내용은 그 스크립트 실행 결과 참고.
3. **급등 전 숏스퀴즈 후보** — "오늘 급등 가능성이 있고, 급등하면 숏스퀴즈로
   이어질 수 있는" 종목. 세 조건 모두 충족: 공매도비중(Short Float) 20% 이상
   (스퀴즈 구조) + 오늘 상대거래량 1.5배 이상(실제로 오늘 거래가 몰리는 중) +
   등락률 -3%~+6%(아직 안 터짐 — relvol이 높아도 이미 +100%처럼 크게 움직인
   종목은 "오늘 이미 터진" 거라 제외). 공매도비중 x 커버일수(Short Ratio) x
   오늘 상대거래량을 곱한 "스퀴즈 점수" 내림차순. 조건이 셋 다 걸려서 리스트가
   짧을 수 있음(며칠은 0개일 수도 있음) — 원래 희소한 조건이라 정상. 레버리지·
   인버스 ETF는 제외.

## 국내(코스피+코스닥, 네이버 증권)

**업종 강도**만 우선 지원 — 업종(~80개, 네이버 WICS 분류) 당일 등락률 기준
강한/약한 업종, 최근 연속 강세 업종. 트리맵 크기는 시가총액 대신 업종 소속
종목수로 근사(네이버 업종별 시세는 시총을 안 줌). 급등 전 후보/숏스퀴즈 후보는
아직 없음 — 국내는 레버리지 ETF가 거래상위를 다 차지해서 미국(Finviz
`ind_stocksonly`)처럼 깔끔히 걸러지지 않고, 숏스퀴즈용 공매도 잔고 데이터는
pykrx 쪽 엔드포인트가 현재 깨져 있어(2026-08 확인) 나중에 별도로 다룰 예정.

## 구성

- `scripts/collect_sector_snapshot.py` — Finviz 스크래핑 후 `data/sector_history.json`에
  업종 스냅샷 추가 (최근 60개 = 약 30일치 유지, 빈 응답이면 기존 데이터 보존)
- `scripts/collect_presurge_screener.py` — Finviz 스크리너 스크래핑 후
  `data/presurge_history.json`에 급등 전 후보 스냅샷 추가 (같은 보존 정책)
- `scripts/collect_shortsqueeze_screener.py` — Finviz 스크리너 스크래핑 후
  `data/shortsqueeze_history.json`에 숏스퀴즈 후보 스냅샷 추가 (같은 보존 정책)
- `scripts/collect_kr_sector_snapshot.py` — 네이버 증권 스크래핑 후
  `data/kr_sector_history.json`에 국내 업종 스냅샷 추가 (같은 보존 정책)
- `data/sector_history.json`, `data/presurge_history.json`, `data/shortsqueeze_history.json`,
  `data/kr_sector_history.json` — 스냅샷 히스토리
- `index.html` — 정적 대시보드 (빌드 단계 없음). 상단 해외/국내 토글로
  `DATA.overseas`/`DATA.domestic` 중 하나를 렌더링. 해외 쪽은 네 JSON을 fetch해서
  라벨+KST 날짜로 서로 매칭
- `.github/workflows/collect.yml` — 하루 2회(+버퍼 cron) 네 스크립트 실행 후 커밋/푸시

## GitHub Pages 배포 방법

1. 이 저장소를 GitHub에 push (`main` 브랜치)
2. 저장소 → **Settings** → **Pages** → Source: **Deploy from a branch**,
   Branch: **main** / **/ (root)** → Save
3. 저장소 → **Settings** → **Actions** → **General** → **Workflow permissions** →
   **Read and write permissions** 선택 → Save (자동 커밋에 필요)
4. **Actions** 탭 → `데이터 수집 (해외 업종/급등전/숏스퀴즈 + 국내 업종)` → **Run workflow** 로 첫 데이터 수집 실행
5. 약 1~2분 후 `https://dlgksgml1158-commits.github.io/stock/` 에서 확인

## 로컬 실행

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install requests beautifulsoup4 lxml
python scripts/collect_sector_snapshot.py --label manual
python scripts/collect_presurge_screener.py --label manual
python scripts/collect_shortsqueeze_screener.py --label manual
python scripts/collect_kr_sector_snapshot.py --label manual
python -m http.server 8000   # 이후 http://localhost:8000 접속
```
