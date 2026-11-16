"""보건 데일리 자동 수집 프로그램.

매일 GitHub Actions에서 실행됩니다.
1) sources.yml 의 구글 뉴스 검색과 기관 게시판에서 새 글 목록을 모읍니다.
2) 이미 올린 글을 걸러 냅니다.
3) Gemini(무료 등급)에 보내 관련성, 분류, 요약, 실무 포인트를 받습니다.
   AI를 쓸 수 없으면 제목과 RSS 설명만으로 올립니다.
4) data/items.json, data/status.json, data/seen.json 을 갱신합니다.
5) 매월 첫 실행 때 data/monthly.json 에 그 달의 '챙길 일'을 만듭니다.

기사 본문은 저장하지 않습니다. 제목, 짧은 요약, 원문 링크만 저장합니다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus, urljoin

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.astimezone(KST).date()
CATS = ["news", "law", "chem", "notice", "case", "fund", "edu", "job", "info"]
UA = {"User-Agent": "Mozilla/5.0 (health-daily collector; +https://github.com)"}
LOG: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)
    LOG.append(msg)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def clean(text: str, limit: int = 400) -> str:
    text = BeautifulSoup(html.unescape(text or ""), "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def norm_title(t: str) -> str:
    t = re.sub(r"\s+-\s+[^-]{1,30}$", "", t or "")  # 구글 뉴스의 ' - 매체명' 꼬리 제거
    return re.sub(r"[\W_]+", "", t).lower()


def key_of(url: str, title: str) -> str:
    return hashlib.sha1((norm_title(title) or url).encode("utf-8")).hexdigest()[:16]


DATE_RE = re.compile(r"(20\d{2})[.\-/년 ]\s*(\d{1,2})[.\-/월 ]\s*(\d{1,2})")


def find_date(text: str) -> str | None:
    m = DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def valid_date(s) -> str | None:
    if isinstance(s, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            dt.date.fromisoformat(s)
            return s
        except ValueError:
            return None
    return None


# ───────────────────────── 수집 ─────────────────────────

def collect_news(cfg) -> list[dict]:
    out = []
    days = int(cfg.get("lookback_days", 3))
    for q in cfg.get("news_queries", []):
        url = (
            "https://news.google.com/rss/search?q="
            + quote_plus(f"{q['q']} when:{days}d")
            + "&hl=ko&gl=KR&ceid=KR:ko"
        )
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
        except Exception as e:  # noqa: BLE001
            log(f"[뉴스] '{q['q']}' 실패: {e}")
            continue
        n = 0
        for e in feed.entries[:15]:
            title = clean(e.get("title", ""), 200)
            source = clean((e.get("source") or {}).get("title", ""), 60)
            if source and title.endswith(" - " + source):
                title = title[: -len(" - " + source)]
            date = None
            if e.get("published_parsed"):
                date = dt.datetime(*e.published_parsed[:6], tzinfo=dt.timezone.utc).astimezone(KST).date().isoformat()
            out.append({
                "title": title, "url": e.get("link", ""), "source": source or "뉴스",
                "date": date, "snippet": clean(e.get("summary", ""), 300), "hint": q.get("hint", "news"),
            })
            n += 1
        log(f"[뉴스] '{q['q']}' {n}건")
        time.sleep(1)
    return out


def collect_boards(cfg) -> list[dict]:
    out = []
    since = TODAY - dt.timedelta(days=int(cfg.get("lookback_days", 3)) + 2)
    filt = cfg.get("board_filter_words") or []
    for b in cfg.get("boards", []):
        try:
            r = requests.get(b["url"], headers=UA, timeout=25)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:  # noqa: BLE001
            log(f"[게시판] {b['name']} 열기 실패: {e}")
            continue
        n = 0
        seen_urls = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if b["link"] not in href or href.startswith("javascript"):
                continue
            url = urljoin(b["url"], href)
            if url in seen_urls:
                continue
            title = clean(a.get_text(" "), 200)
            if len(title) < 6:
                continue
            row = a.find_parent("tr") or a.find_parent("li") or a.parent
            date = find_date(row.get_text(" ") if row else "")
            if date and dt.date.fromisoformat(date) < since:
                continue
            words = b.get("keywords") or filt
            if words and not any(w in title for w in words):
                continue
            seen_urls.add(url)
            out.append({"title": title, "url": url, "source": b["name"], "date": date,
                        "snippet": "", "hint": b.get("hint", "notice")})
            n += 1
        log(f"[게시판] {b['name']} {n}건" + ("  ← 링크를 찾지 못했습니다. 게시판 구조가 바뀌었을 수 있습니다." if n == 0 else ""))
        time.sleep(1)
    return out


# ───────────────────────── Gemini ─────────────────────────

PROMPT = """당신은 한국 제조업 사업장 보건관리자(산업간호사 등)를 위한 공용 웹앱 '보건 데일리'의 편집자입니다.
아래 후보 목록(JSON)은 오늘 모은 새 글의 제목, 출처, 게시일, 짧은 설명입니다. 각 후보를 판단해 JSON 배열로만 답하세요.

판단 기준:
- keep: 사업장 보건관리자 업무(산업보건, 직업병, 건강진단, 화학물질, 근골격계, 직무스트레스, 온열·한랭질환, 감염병의 사업장 대응, 산업안전보건 법령·감독, 산재, 보건 관련 교육·행사·지원사업·채용)와 관련 있으면 true. 연예·정치 일반·광고·병원 홍보·보건과 무관한 노동 기사는 false.
- cat: news(뉴스) | law(법령·입법예고) | chem(화학물질 규제) | notice(정부·기관 공지·지침) | case(재해·직업병 사례, 판결) | fund(지원사업 공고) | edu(교육·세미나·학술대회·설명회) | job(채용) | info(자료·통계·KOSHA GUIDE)
- imp: 3(법령 개정·화학물질 규제 변경·건강진단 제도 변경·감독 강화처럼 업무에 직접 영향), 2(알아두면 좋은 정책·지원사업·직무교육·설명회·채용), 1(참고)
- summary: 1~2문장 한국어 요약. 주어진 제목과 설명에 있는 사실만 쓰고, 원문 문장을 그대로 옮기지 말고 바꿔 씁니다. 설명이 없으면 제목을 풀어 쓴 1문장.
- point: 제조업 사업장 보건관리자가 무엇을 하면 좋은지 1문장. 근거가 부족하면 빈 문자열. 채용(job)은 항상 빈 문자열.
- tags: 키워드 2~4개. 채용·교육·지원사업은 첫 태그에 지역(시·도, 온라인이면 "온라인")을 알 수 있을 때만 넣습니다.
- deadline / eventDate / eventEnd: 주어진 글에 날짜가 명시된 경우에만 "YYYY-MM-DD". 추측 금지. 없으면 null.
- 개인 이름, 피해자 신원 등 개인을 알아볼 수 있는 정보는 쓰지 않습니다.

출력 형식(후보마다 하나, k는 후보의 k 그대로):
[{"k":"...","keep":true,"cat":"news","imp":2,"summary":"...","point":"...","tags":["..."],"deadline":null,"eventDate":null,"eventEnd":null}]

후보:
"""


def gemini_call(models: list[str], prompt: str) -> tuple[str | None, str | None]:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None, "GEMINI_API_KEY 없음"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
    }
    last = None
    for m in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
        for attempt in range(2):
            try:
                r = requests.post(url, params={"key": key}, json=body, timeout=90)
            except Exception as e:  # noqa: BLE001
                last = f"{m}: {e}"
                break
            if r.status_code == 429 and attempt == 0:
                time.sleep(30)
                continue
            if r.status_code != 200:
                last = f"{m}: HTTP {r.status_code}"
                break
            try:
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                return text, m
            except Exception as e:  # noqa: BLE001
                last = f"{m}: 응답 해석 실패 {e}"
                break
    return None, last


def enrich(cands: list[dict], cfg) -> tuple[list[dict], str]:
    g = cfg.get("gemini", {})
    models = g.get("models") or ["gemini-flash-lite-latest"]
    size = int(g.get("batch_size", 20))
    results: dict[str, dict] = {}
    used_ai = None
    for i in range(0, len(cands), size):
        batch = cands[i:i + size]
        payload = [{"k": c["k"], "title": c["title"], "source": c["source"], "date": c["date"],
                    "desc": c["snippet"], "hint": c["hint"]} for c in batch]
        text, info = gemini_call(models, PROMPT + json.dumps(payload, ensure_ascii=False))
        if text is None:
            log(f"[AI] 요약 실패 ({info}) — 이 묶음은 제목과 설명만 올립니다.")
            continue
        used_ai = info
        try:
            arr = json.loads(text)
            for x in arr if isinstance(arr, list) else []:
                if isinstance(x, dict) and x.get("k"):
                    results[str(x["k"])] = x
        except Exception as e:  # noqa: BLE001
            log(f"[AI] JSON 해석 실패: {e}")
        time.sleep(5)
    items = []
    for c in cands:
        x = results.get(c["k"])
        if x is not None and x.get("keep") is False:
            continue
        cat = x.get("cat") if x and x.get("cat") in CATS else c["hint"]
        try:
            imp = int(x.get("imp", 1)) if x else 1
        except (TypeError, ValueError):
            imp = 1
        it = {
            "id": c["k"], "cat": cat, "imp": max(1, min(3, imp)),
            "date": c["date"] or TODAY.isoformat(), "source": c["source"], "url": c["url"],
            "title": c["title"],
            "summary": clean((x or {}).get("summary") or c["snippet"] or "", 300),
            "point": "" if cat == "job" else clean((x or {}).get("point") or "", 200),
            "tags": [clean(t, 20) for t in ((x or {}).get("tags") or [])][:4],
            "addedAt": NOW.isoformat(timespec="seconds"),
        }
        for f in ("deadline", "eventDate", "eventEnd"):
            v = valid_date((x or {}).get(f))
            if v:
                it[f] = v
        if it.get("deadline") and it["deadline"] < TODAY.isoformat():
            continue  # 마감이 지난 공고·교육은 올리지 않음
        if cat == "edu" and it.get("eventEnd", it.get("eventDate", "9999")) < TODAY.isoformat():
            continue  # 이미 끝난 행사
        items.append(it)
    return items, (used_ai or "AI 미사용")


# ───────────────────────── 이번 달 챙길 일 ─────────────────────────

MONTHLY_PROMPT = """한국 제조업 사업장 보건관리자가 {ym}에 챙길 일 5~7개를 JSON 배열로만 답하세요.
아래 '확인된 기준'에 있는 내용만 법정 근거로 쓰고, 그 밖의 수치·기한은 쓰지 마세요. 특정 날짜를 지어내지 마세요.
각 항목: {{"id":"짧은영문","kind":"법정" 또는 "실무","title":"20자 안팎","detail":"무엇을 확인·준비할지 2~3문장","basis":"근거 조문 또는 출처","url":"근거 URL"}}
법정 항목의 url 은 https://www.law.go.kr/법령/산업안전보건법시행규칙 처럼 국가법령정보센터 주소를 씁니다.

확인된 기준:
- 작업환경측정: 반기 1회 이상, 결과 보고는 시료채취를 마친 날부터 30일 이내 (산업안전보건법 제125조, 시행규칙 제188조·제190조)
- 근로자 정기 안전보건교육: 매반기, 사무직·판매직 6시간 이상, 그 밖의 근로자 12시간 이상. 관리감독자 연간 16시간 이상 (시행규칙 별표 4)
- 산업안전보건위원회 정기회의: 분기마다 (시행령 제37조)
- 일반건강진단: 사무직 2년에 1회, 그 밖의 근로자 1년에 1회 (시행규칙 제197조). 특수건강진단은 유해인자별 주기 (시행규칙 별표 23)
- 근골격계 유해요인조사: 3년마다 (산업안전보건기준에 관한 규칙 제657조)
- 달별 흐름: 1월 연간 계획·전년도 실적, 2~3월 상반기 측정·검진 일정과 계약, 3·6·9·12월 분기 위원회, 5월 폭염 대비, 6~9월 온열질환 예방, 6월·12월 반기 교육 이수 마감, 9~11월 하반기 측정·검진 마무리와 독감 대비, 11월 한파 대비와 내년 계획·예산, 12월 연간 실적 정리
"""


def ensure_monthly(cfg) -> None:
    path = DATA / "monthly.json"
    allm = load_json(path, {})
    ym = TODAY.strftime("%Y-%m")
    if ym in allm:
        return
    text, info = gemini_call(cfg.get("gemini", {}).get("models") or ["gemini-flash-lite-latest"],
                             MONTHLY_PROMPT.format(ym=f"{TODAY.year}년 {TODAY.month}월"))
    if not text:
        log(f"[이번 달] 만들지 못했습니다 ({info}). 내일 다시 시도합니다.")
        return
    try:
        arr = [x for x in json.loads(text) if isinstance(x, dict) and x.get("title")][:7]
    except Exception as e:  # noqa: BLE001
        log(f"[이번 달] JSON 해석 실패: {e}")
        return
    for n, x in enumerate(arr):
        x["id"] = re.sub(r"[^a-z0-9-]", "", str(x.get("id", "")).lower()) or f"t{n}"
        x["kind"] = "법정" if x.get("kind") == "법정" else "실무"
        if not str(x.get("url", "")).startswith("https://"):
            x.pop("url", None)
    allm[ym] = {"month": ym, "updatedAt": NOW.isoformat(timespec="seconds"), "items": arr}
    for k in sorted(allm)[:-13]:  # 13개월치만 보관
        del allm[k]
    save_json(path, allm)
    log(f"[이번 달] {ym} 챙길 일 {len(arr)}개 작성")


# ───────────────────────── 실행 ─────────────────────────

def main() -> int:
    cfg = yaml.safe_load((ROOT / "sources.yml").read_text(encoding="utf-8"))
    store = load_json(DATA / "items.json", {"days": []})
    days = {d["date"]: d for d in store.get("days", []) if d.get("date")}
    seen = set(load_json(DATA / "seen.json", []))
    for d in days.values():
        for it in d.get("items", []):
            if it.get("id"):
                seen.add(it["id"])
            seen.add(key_of(it.get("url", ""), it.get("title", "")))

    raw = collect_news(cfg) + collect_boards(cfg)
    since = (TODAY - dt.timedelta(days=int(cfg.get("lookback_days", 3)))).isoformat()
    cands, keys = [], set()
    for c in raw:
        if not c["title"] or not c["url"].startswith("http"):
            continue
        if c["date"] and c["date"] < since and c["hint"] != "edu":
            continue
        k = key_of(c["url"], c["title"])
        if k in seen or k in keys:
            continue
        keys.add(k)
        c["k"] = k
        cands.append(c)
    cands = cands[: int(cfg.get("gemini", {}).get("max_candidates", 80))]
    log(f"[정리] 모은 글 {len(raw)}건 → 새 후보 {len(cands)}건")

    items, ai = enrich(cands, cfg) if cands else ([], "후보 없음")
    items.sort(key=lambda x: (x["imp"], x["date"]), reverse=True)  # 중요도, 최신순
    items = items[: int(cfg.get("gemini", {}).get("max_items_per_day", 30))]

    today = TODAY.isoformat()
    if items:
        d = days.setdefault(today, {"date": today, "items": []})
        d["items"].extend(items)
        d["updatedAt"] = NOW.isoformat(timespec="seconds")
    for it in items:
        seen.add(it["id"])
    seen.update(keys)  # 관련 없다고 판단된 글도 다시 보내지 않음

    cutoff = (TODAY - dt.timedelta(days=int(cfg.get("keep_days", 120)))).isoformat()
    kept = sorted((d for d in days.values() if d["date"] >= cutoff), key=lambda d: d["date"], reverse=True)
    save_json(DATA / "items.json", {"updatedAt": NOW.isoformat(timespec="seconds"), "days": kept})
    save_json(DATA / "seen.json", sorted(seen)[-8000:] if len(seen) > 8000 else sorted(seen))

    ensure_monthly(cfg)

    imp3 = [i["title"] for i in items if i["imp"] >= 3]
    note = f"새 소식 {len(items)}건 ({ai})" + (f" · 중요: {imp3[0]}" if imp3 else "")
    save_json(DATA / "status.json", {"lastRun": NOW.isoformat(timespec="seconds"), "lastCount": len(items),
                                     "note": note, "log": LOG[-60:]})
    log(note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
