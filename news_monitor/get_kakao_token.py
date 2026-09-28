"""카카오 리프레시 토큰을 처음 한 번 발급받는 도우미.

사용법:
  python news_monitor/get_kakao_token.py <REST_API_KEY> [CLIENT_SECRET]

1) 출력된 주소를 브라우저에서 열고 카카오 로그인 + '카카오톡 메시지 전송' 동의
2) 이동된 주소창의 code=... 값을 복사해 붙여넣기
3) 출력된 refresh_token 을 GitHub Secret KAKAO_REFRESH_TOKEN 에 등록
"""

import json
import sys
import urllib.parse
import urllib.request

REDIRECT_URI = "https://localhost"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    rest_key = sys.argv[1]
    secret = sys.argv[2] if len(sys.argv) > 2 else ""

    auth_url = "https://kauth.kakao.com/oauth/authorize?" + urllib.parse.urlencode(
        {"client_id": rest_key, "redirect_uri": REDIRECT_URI, "response_type": "code", "scope": "talk_message"}
    )
    print("1) 아래 주소를 브라우저에서 여세요:\n\n" + auth_url + "\n")
    raw = input("2) 이동된 주소 전체(또는 code 값)를 붙여넣으세요: ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query).get("code", [raw])[0]

    data = {"grant_type": "authorization_code", "client_id": rest_key, "redirect_uri": REDIRECT_URI, "code": code}
    if secret:
        data["client_secret"] = secret
    req = urllib.request.Request("https://kauth.kakao.com/oauth/token", data=urllib.parse.urlencode(data).encode())
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            token = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"발급 실패: {e.read().decode(errors='replace')}")

    print("\n3) GitHub Secret KAKAO_REFRESH_TOKEN 에 아래 값을 등록하세요:\n")
    print(token["refresh_token"])
    days = token.get("refresh_token_expires_in", 0) // 86400
    print(f"\n(유효기간 약 {days}일)")


if __name__ == "__main__":
    main()
