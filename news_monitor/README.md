# 네이버 뉴스 → 카카오톡 알림

10분마다 네이버 뉴스에서 키워드(`.github/workflows/news-monitor.yml`의 `KEYWORDS`)를 검색해
새 기사만 카카오톡 "나와의 채팅"으로 보냅니다. 첫 실행에는 시작 알림만 보냅니다.

## 필요한 GitHub Secrets

| 이름 | 내용 |
|---|---|
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | 네이버 개발자센터 앱(검색 API) |
| `KAKAO_REST_API_KEY` | 카카오 개발자 앱 REST API 키 |
| `KAKAO_REFRESH_TOKEN` | `python news_monitor/get_kakao_token.py <REST_API_KEY>`로 발급 |
| `KAKAO_CLIENT_SECRET` | (선택) 카카오 앱에서 Client Secret을 켠 경우 |
| `GH_PAT` | (선택) 리프레시 토큰 자동 갱신용, 이 저장소 Secrets 쓰기 권한 |

## 로컬 테스트

```bash
NAVER_CLIENT_ID=... NAVER_CLIENT_SECRET=... KEYWORDS="검찰|기소" DRY_RUN=1 python news_monitor/monitor.py
```

GitHub의 예약 실행(schedule)은 저장소 **기본 브랜치**에 이 워크플로 파일이 있어야 동작합니다.
