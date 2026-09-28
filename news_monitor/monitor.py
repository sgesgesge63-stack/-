"""네이버 뉴스 키워드 모니터링 → 카카오톡 '나에게 보내기' 알림.

GitHub Actions에서 주기적으로 실행하는 것을 전제로 한다(표준 라이브러리만 사용).

환경 변수
  NAVER_CLIENT_ID, NAVER_CLIENT_SECRET   네이버 검색 API 키
  KAKAO_REST_API_KEY                     카카오 앱 REST API 키
  KAKAO_REFRESH_TOKEN                    카카오 리프레시 토큰 (talk_message 동의 포함)
  KAKAO_CLIENT_SECRET                    (선택) 카카오 앱에서 Client Secret을 켠 경우
  KEYWORDS                               '|'로 구분한 키워드 목록
  EXCLUDE_KEYWORDS                       (선택) 제목/요약에 있으면 버릴 단어, '|' 구분
  STATE_FILE                             이미 본 기사 목록 파일 (기본 state/seen.json)
  MAX_MESSAGES                           한 번 실행에 보낼 최대 기사 수 (기본 15)
  LOOKBACK_MINUTES                       이보다 오래된 기사는 무시 (기본 180)
  NEW_REFRESH_TOKEN_FILE                 리프레시 토큰이 갱신되면 새 값을 쓸 파일
  DRY_RUN=1                              카카오 전송 없이 콘솔에만 출력
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

KST = timezone(timedelta(hours=9))
TAG_RE = re.compile(r"<[^>]+>")
SEEN_TTL_DAYS = 3  # 이 기간이 지난 기록은 상태 파일에서 정리
KAKAO_TEXT_LIMIT = 200  # 카카오 텍스트 템플릿 본문 최대 길이


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        sys.exit(f"환경 변수 {name} 가 설정되지 않았습니다.")
    return value or ""


def split_list(raw: str) -> list[str]:
    return [w.strip() for w in raw.split("|") if w.strip()]


def http_json(url: str, data: dict | None = None, headers: dict | None = None) -> dict:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"{url} → HTTP {e.code}: {detail}") from None


def clean(text: str) -> str:
    return html.unescape(TAG_RE.sub("", text)).strip()


def title_key(title: str) -> str:
    """언론사마다 조금씩 다른 같은 기사를 한 번만 보내기 위한 키."""
    return re.sub(r"[\W_]+", "", title).lower()[:40]


# ---------------------------------------------------------------- 네이버


def search_news(keyword: str, client_id: str, client_secret: str) -> list[dict]:
    query = urllib.parse.urlencode({"query": keyword, "display": 100, "sort": "date"})
    data = http_json(
        f"{NAVER_NEWS_URL}?{query}",
        headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret},
    )
    articles = []
    for item in data.get("items", []):
        try:
            published = parsedate_to_datetime(item["pubDate"])
        except (KeyError, TypeError, ValueError):
            published = datetime.now(timezone.utc)
        articles.append(
            {
                "title": clean(item.get("title", "")),
                "summary": clean(item.get("description", "")),
                # 네이버 뉴스 링크가 있으면 그쪽(카카오 도메인 등록이 쉬움), 없으면 원문
                "link": item.get("link") or item.get("originallink", ""),
                "originallink": item.get("originallink", ""),
                "published": published,
                "keyword": keyword,
            }
        )
    return articles


def collect(keywords: list[str], excludes: list[str], lookback: timedelta) -> list[dict]:
    client_id = env("NAVER_CLIENT_ID", required=True)
    client_secret = env("NAVER_CLIENT_SECRET", required=True)
    cutoff = datetime.now(timezone.utc) - lookback

    merged: dict[str, dict] = {}
    for kw in keywords:
        try:
            results = search_news(kw, client_id, client_secret)
        except RuntimeError as e:
            print(f"[경고] '{kw}' 검색 실패: {e}", file=sys.stderr)
            continue
        for a in results:
            if a["published"] < cutoff:
                continue
            text = a["title"] + " " + a["summary"]
            if any(x in text for x in excludes):
                continue
            key = a["link"]
            if key in merged:
                if kw not in merged[key]["keywords"]:
                    merged[key]["keywords"].append(kw)
            else:
                a["keywords"] = [kw]
                merged[key] = a
        time.sleep(0.1)  # 초당 호출 제한 여유

    # 태그는 제목·요약에 실제로 보이는 키워드 위주로 (없으면 검색에 걸린 키워드)
    for a in merged.values():
        text = a["title"] + " " + a["summary"]
        visible = [kw for kw in keywords if kw in text]
        a["keywords"] = (visible or a["keywords"])[:3]

    return sorted(merged.values(), key=lambda a: a["published"])


# ---------------------------------------------------------------- 상태


def load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_state(path: Path, state: dict) -> None:
    cutoff = time.time() - SEEN_TTL_DAYS * 86400
    for bucket in ("links", "titles"):
        state[bucket] = {k: t for k, t in state.get(bucket, {}).items() if t >= cutoff}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- 카카오


def kakao_access_token() -> str:
    data = {
        "grant_type": "refresh_token",
        "client_id": env("KAKAO_REST_API_KEY", required=True),
        "refresh_token": env("KAKAO_REFRESH_TOKEN", required=True),
    }
    secret = env("KAKAO_CLIENT_SECRET")
    if secret:
        data["client_secret"] = secret
    token = http_json(KAKAO_TOKEN_URL, data=data)

    # 리프레시 토큰 만료가 1개월 이내로 남으면 카카오가 새 리프레시 토큰을 준다.
    new_refresh = token.get("refresh_token")
    out = env("NEW_REFRESH_TOKEN_FILE")
    if new_refresh and out:
        Path(out).write_text(new_refresh, encoding="utf-8")
        print("[안내] 카카오 리프레시 토큰이 갱신되었습니다.")
    return token["access_token"]


def kakao_send(access_token: str, text: str, url: str) -> None:
    template = {
        "object_type": "text",
        "text": text[:KAKAO_TEXT_LIMIT],
        "link": {"web_url": url, "mobile_web_url": url},
        "button_title": "기사 보기",
    }
    res = http_json(
        KAKAO_MEMO_URL,
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if res.get("result_code") != 0:
        raise RuntimeError(f"카카오 전송 실패: {res}")


def format_article(a: dict) -> str:
    when = a["published"].astimezone(KST).strftime("%m/%d %H:%M")
    tags = " ".join(f"#{k}" for k in a["keywords"])
    head = f"[{tags}] {when}\n{a['title']}\n"
    url = a["link"]
    room = KAKAO_TEXT_LIMIT - len(head) - len(url) - 2
    summary = a["summary"]
    if room < 10:
        summary = ""
    elif len(summary) > room:
        summary = summary[: room - 1] + "…"
    return f"{head}{summary}\n{url}" if summary else f"{head}{url}"


# ---------------------------------------------------------------- main


def main() -> None:
    keywords = split_list(env("KEYWORDS", required=True))
    excludes = split_list(env("EXCLUDE_KEYWORDS"))
    state_path = Path(env("STATE_FILE", "state/seen.json"))
    max_messages = int(env("MAX_MESSAGES", "15"))
    lookback = timedelta(minutes=int(env("LOOKBACK_MINUTES", "180")))
    dry_run = env("DRY_RUN") == "1"

    articles = collect(keywords, excludes, lookback)
    state = load_state(state_path)
    first_run = state is None
    state = state or {"links": {}, "titles": {}}
    now = time.time()

    fresh = []
    for a in articles:
        tkey = title_key(a["title"])
        if a["link"] in state["links"] or tkey in state["titles"]:
            continue
        state["links"][a["link"]] = now
        state["titles"][tkey] = now
        fresh.append(a)

    print(f"수집 {len(articles)}건, 새 기사 {len(fresh)}건 (첫 실행: {first_run})")

    to_send: list[tuple[str, str]] = []
    search_url = "https://search.naver.com/search.naver?where=news&sort=1&query=" + urllib.parse.quote(
        " | ".join(keywords)
    )
    if first_run:
        # 첫 실행에는 과거 기사를 쏟아내지 않고 시작 알림만 보낸다.
        to_send.append(
            (
                f"뉴스 모니터링을 시작합니다.\n키워드 {len(keywords)}개: {', '.join(keywords)}\n"
                f"지금부터 새로 올라오는 기사를 보내드립니다.",
                search_url,
            )
        )
    else:
        for a in fresh[:max_messages]:
            to_send.append((format_article(a), a["link"]))
        if len(fresh) > max_messages:
            to_send.append(
                (f"외 {len(fresh) - max_messages}건의 새 기사가 더 있습니다. 네이버에서 최신순으로 확인하세요.", search_url)
            )

    if to_send:
        if dry_run:
            for text, _ in to_send:
                print("-" * 40 + "\n" + text)
        else:
            token = kakao_access_token()
            for text, url in to_send:
                kakao_send(token, text, url)
                time.sleep(0.3)

    # 전송이 끝난 뒤에만 상태 저장 (실패 시 다음 실행에서 재시도)
    save_state(state_path, state)


if __name__ == "__main__":
    main()
