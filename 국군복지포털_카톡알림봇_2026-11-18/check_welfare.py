import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.welfare.mil.kr"
MAIN_URL = "https://www.welfare.mil.kr/main.do"
BOARD_LIST_URL = "https://www.welfare.mil.kr/board/board.do?m_code=122&be_id=c_notice"
STATE_FILE = Path(__file__).resolve().parent / "seen_notices.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


def fetch_notices() -> list[dict]:
    print(f"[1] 국군복지포털 접속 시도 중... ({MAIN_URL})")
    try:
        resp = requests.get(MAIN_URL, headers=HEADERS, timeout=25)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        print("\n========================================================")
        print("❌ 국군복지포털 접속 실패!")
        print("원인 안내: 국방/군 포털(.mil.kr)의 해외 IP 차단(Geo-Blocking)으로 인해")
        print("GitHub 미국 서버에서의 접근이 차단되었을 가능성이 높습니다.")
        print(f"상세 오류: {exc}")
        print("========================================================")
        sys.exit(1)

    html = resp.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    links = soup.select("#location1 > ul > li > a")

    if not links:
        print("⚠️ #location1 공지사항 목록을 찾지 못했습니다. 페이지 구조를 확인해 주세요.")
        sys.exit(1)

    notices = []
    for a_tag in links:
        href = a_tag.get("href", "").strip()
        full_url = urljoin(BASE_URL, href) if href else BOARD_LIST_URL
        parsed_qs = parse_qs(urlparse(full_url).query)
        bm_serial = parsed_qs.get("bm_serial", [full_url])[0]

        p_tag = a_tag.find("p")
        title = " ".join(p_tag.get_text(" ", strip=True).split()) if p_tag else "(제목 없음)"

        date_spans = a_tag.find_all("span", recursive=False)
        raw_date = date_spans[-1].get_text(strip=True) if date_spans else ""
        clean_date = raw_date.replace(" .", ".").replace(". ", ".").strip()

        notices.append(
            {
                "id": bm_serial,
                "title": title,
                "date": clean_date,
                "url": full_url,
            }
        )

    print(f"✅ 접속 및 파싱 성공! 현재 메인 공지 {len(notices)}건 확인됨:")
    for idx, item in enumerate(notices, start=1):
        print(f"   {idx}. [{item['date']}] {item['title']} (ID: {item['id']})")

    return notices


def get_kakao_access_token(
    rest_api_key: str, refresh_token: str, client_secret: str = ""
) -> tuple[str, int | None]:
    token_url = "https://kauth.kakao.com/oauth/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": rest_api_key,
        "refresh_token": refresh_token,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    resp = requests.post(token_url, data=payload, timeout=15)
    data = resp.json()
    if resp.status_code != 200 or "access_token" not in data:
        print(f"❌ 카카오 액세스 토큰 갱신 실패: {data}")
        sys.exit(1)

    rt_expires_in = data.get("refresh_token_expires_in")
    return data["access_token"], rt_expires_in


def send_kakao_memo(access_token: str, notice: dict, is_test: bool = False, expiry_days: int | None = None) -> None:
    send_url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    prefix = "🧪 [연동 테스트] 국군복지포털 최신 공지" if is_test else "📢 [국군복지포털 새 공지]"

    body_lines = [
        prefix,
        "",
        f"• 제목: {notice['title']}",
        f"• 등록일: {notice['date']}",
        f"• 링크: {notice['url']}",
    ]
    if expiry_days is not None and expiry_days <= 7:
        body_lines.append("")
        body_lines.append(f"⚠️ 카카오 리프레시 토큰 만료가 약 {expiry_days}일 남았습니다. 토큰을 재발급해 주세요.")

    template_obj = {
        "object_type": "text",
        "text": "\n".join(body_lines),
        "link": {
            "web_url": notice["url"],
            "mobile_web_url": notice["url"],
        },
        "button_title": "공지 확인하기",
    }

    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.post(
        send_url,
        headers=headers,
        data={"template_object": json.dumps(template_obj, ensure_ascii=False)},
        timeout=15,
    )
    res_json = resp.json()
    if resp.status_code != 200 or res_json.get("result_code") != 0:
        print(f"❌ 카카오톡 발송 실패: {res_json}")
        sys.exit(1)

    print(f"📩 카카오톡 발송 완료: {notice['title']}")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"initialized": False, "seen_ids": []}


def save_state(seen_ids: list[str]) -> None:
    state = {
        "initialized": True,
        "seen_ids": seen_ids[-200:],
    }
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    notices = fetch_notices()

    rest_api_key = os.environ.get("KAKAO_REST_API_KEY", "").strip()
    refresh_token = os.environ.get("KAKAO_REFRESH_TOKEN", "").strip()
    client_secret = os.environ.get("KAKAO_CLIENT_SECRET", "").strip()
    force_test = os.environ.get("FORCE_TEST_SEND", "false").strip().lower() == "true"

    if not rest_api_key or not refresh_token:
        print("\nℹ️ 아직 GitHub Secrets에 KAKAO_REST_API_KEY / KAKAO_REFRESH_TOKEN이 등록되지 않았습니다.")
        print("✅ 1단계(GitHub 서버 -> 국군복지포털 접속 테스트)가 완벽히 통과되었습니다!")
        print("👉 이제 2단계(카카오 토큰 등록)만 마치시면 새 글 알림이 카톡으로 발송됩니다.")
        return

    state = load_state()
    seen_ids: list[str] = state.get("seen_ids", [])
    initialized: bool = state.get("initialized", False)

    access_token, rt_expires_sec = get_kakao_access_token(rest_api_key, refresh_token, client_secret)
    expiry_days = (rt_expires_sec // 86400) if isinstance(rt_expires_sec, int) else None

    if force_test and notices:
        print("\n[테스트 모드] 최신 공지 1건을 카카오톡으로 테스트 발송합니다...")
        send_kakao_memo(access_token, notices[0], is_test=True, expiry_days=expiry_days)

    if not initialized:
        for n in reversed(notices):
            if n["id"] not in seen_ids:
                seen_ids.append(n["id"])
        save_state(seen_ids)
        print("\n📌 최초 실행 기준점 저장 완료! 이후 새로 올라오는 공지부터 카톡으로 알림을 보냅니다.")
        return

    new_notices = [n for n in reversed(notices) if n["id"] not in seen_ids]
    if not new_notices:
        print("\n✨ 새로 올라온 공지사항이 없습니다.")
        return

    print(f"\n🆕 새 공지 {len(new_notices)}건 발견! 카카오톡 발송을 시작합니다.")
    for item in new_notices:
        send_kakao_memo(access_token, item, is_test=False, expiry_days=expiry_days)
        seen_ids.append(item["id"])

    save_state(seen_ids)


if __name__ == "__main__":
    main()
