# 보건 데일리

산업보건 뉴스, 법령·입법예고, 화학물질 규제, 공지, 재해·직업병 사례, 지원사업, 교육·행사, 채용, 자료를 매일 아침 한곳에 모아 보여 주는 무료 웹앱입니다. 제조업 사업장 보건관리자를 위해 만들었습니다.

- 매일 오전 8시 무렵 GitHub Actions가 자동으로 소식을 모읍니다.
- 요약과 실무 포인트는 Google Gemini API 무료 등급으로 작성합니다.
- 웹앱은 GitHub Pages로 공개되며 로그인 없이 누구나 볼 수 있습니다.

## 처음 설정 (한 번만)

1. **Gemini API 키 등록**
   - Google AI Studio에서 API 키를 만듭니다. 결제(Billing)를 연결하지 않은 프로젝트에서 만들면 한도를 넘어도 요금이 나가지 않습니다.
   - 이 저장소의 **Settings → Secrets and variables → Actions → New repository secret**에서 이름 `GEMINI_API_KEY`, 값에 키를 넣습니다.
   - 키가 없어도 수집은 되지만, 요약·분류·관련성 판단 없이 제목과 설명만 올라갑니다.
2. **GitHub Pages 켜기**
   - **Settings → Pages**에서 Source를 *Deploy from a branch*, Branch를 `main` / `/ (root)`로 두고 Save.
   - 1~2분 뒤 `https://<아이디>.github.io/health-daily/` 에서 열립니다.
3. **Actions 쓰기 권한 확인**
   - **Settings → Actions → General → Workflow permissions**에서 *Read and write permissions*를 선택합니다.
4. **첫 수집 실행**
   - **Actions** 탭 → *보건 데일리 자동 수집* → **Run workflow**.
   - 끝나면 `data/status.json`의 `log`에서 수집처별 결과를 볼 수 있습니다.

## 자주 바꾸는 것

| 바꾸고 싶은 것 | 고칠 파일 |
|---|---|
| 검색 키워드, 기관 게시판 | `sources.yml` |
| 실행 시각 | `.github/workflows/daily.yml`의 `cron` (UTC 기준, `0 23 * * *` = 한국 08:00) |
| 화면, 출처 바로가기, 지역 센터 링크, 면책 문구 | `index.html` |

GitHub 웹에서 파일을 열고 연필(✏️) 버튼으로 고친 뒤 *Commit changes*를 누르면 됩니다.

## 알아 둘 점

- 공개 저장소에서 60일 동안 활동이 없으면 예약 실행이 꺼질 수 있습니다. 매일 데이터가 갱신되면 보통 유지되며, 꺼지면 Actions 탭에서 다시 켭니다.
- 기관이 게시판 구조를 바꾸면 그 수집처만 0건이 됩니다. `data/status.json`의 `log`에 "링크를 찾지 못했습니다"가 보이면 `sources.yml`의 `link` 값을 고쳐야 합니다.
- Gemini 무료 등급에 보낸 내용은 구글 제품 개선에 쓰일 수 있습니다. 이 프로그램은 공개된 기사·게시물의 제목과 짧은 설명만 보내며, 개인정보나 사내 자료는 보내지 않습니다.
- 기사 본문은 저장하지 않고 제목, 짧은 요약, 원문 링크만 저장합니다.

## 파일 구성

```
index.html                    웹앱 화면
data/items.json               날짜별 소식 (자동 갱신)
data/monthly.json             이번 달 챙길 일 (매월 첫 수집 때 자동 작성)
data/status.json              마지막 수집 결과와 로그
data/seen.json                이미 처리한 글 목록 (중복 방지)
sources.yml                   수집 키워드·게시판 설정
scripts/collect.py            수집 프로그램
.github/workflows/daily.yml   매일 실행 설정
```

© 2026 보건관리자 실무노트
