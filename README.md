# 미국 업종 강도 대시보드

Finviz 업종(~140개) 성과 페이지를 하루 2번(미국 장 시작 무렵 / 한국시간 자정) 자동
수집해서, 지금 어느 업종이 강한지/약한지와 최근 연속으로 강세인 업종을 보여주는
정적 웹 대시보드. 로그인/API 키 불필요.

## 구성

- `scripts/collect_sector_snapshot.py` — Finviz 스크래핑 후 `data/sector_history.json`에
  스냅샷 추가 (최근 60개 = 약 30일치 유지, 빈 응답이면 기존 데이터 보존)
- `data/sector_history.json` — 스냅샷 히스토리
- `index.html` — 정적 대시보드 (빌드 단계 없음, `data/sector_history.json`을 fetch)
- `.github/workflows/collect.yml` — 하루 2회 자동 수집 + 커밋/푸시

## GitHub Pages 배포 방법

1. 이 저장소를 GitHub에 push (`main` 브랜치)
2. 저장소 → **Settings** → **Pages** → Source: **Deploy from a branch**,
   Branch: **main** / **/ (root)** → Save
3. 저장소 → **Settings** → **Actions** → **General** → **Workflow permissions** →
   **Read and write permissions** 선택 → Save (자동 커밋에 필요)
4. **Actions** 탭 → `업종 스냅샷 수집` → **Run workflow** 로 첫 데이터 수집 실행
5. 약 1~2분 후 `https://dlgksgml1158-commits.github.io/stock/` 에서 확인

## 로컬 실행

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install requests beautifulsoup4 lxml
python scripts/collect_sector_snapshot.py --label manual
python -m http.server 8000   # 이후 http://localhost:8000 접속
```
