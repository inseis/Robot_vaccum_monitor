#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
로봇청소기 편성 월간 취합 자동화 스크립트
==========================================

hdmzp.github.io/hdhs (홈쇼핑 시청환경조회) 사이트가 공개하는 JSON 데이터를 이용해
HD(현대)/GS/CJ/LT(롯데) 4개 채널의 "로봇청소기" 편성만 걸러서 한 달 치를 모아
보여줍니다. (라이브방송 + 디지털/데이터방송 모두 포함)

사용된 데이터 소스 (공개 정적 JSON, 로그인/인증 불필요):
  https://hdmzp.github.io/hdhs/homeshopping/{COMPANY}_live/{YYYY-MM}.json
  https://hdmzp.github.io/hdhs/homeshopping/{COMPANY}_data/{YYYY-MM}.json
  (COMPANY = HD, GS, CJ, LT)

참고: hsmoa.com, live.ecomm-data.com 도 후보로 검토했는데, hsmoa는 화면이 클라이언트
에서 동적으로 그려지고 공개 JSON API가 바로 안 보였고, live.ecomm-data.com(라방바
데이터랩)은 로그인/구독이 필요한 유료 분석 플랫폼이라 이번 버전에서는 제외했습니다.
hdmzp가 담당자님이 말씀하신 것처럼 가장 안정적이라 이 사이트를 주축으로 삼았습니다.

[로봇청소기 판별 방법]
1) 브랜드가 "로봇청소기 전문 브랜드" 목록(ROBOT_ONLY_BRANDS)에 있으면 무조건 포함
   (예: 로보락, 드리미, 존알, 오로와 등 — 이 브랜드들은 홈쇼핑에서 파는 게 사실상
   로봇청소기 라인업뿐이라, 제품명에 "로봇"이라는 단어가 없어도(F25 ACE 등) 포함)
2) 브랜드가 여러 가전을 만드는 일반 브랜드 목록(GENERIC_BRANDS)에 있으면, 제품명에
   "로봇"이라는 단어가 명시된 경우에만 포함 (예: 삼성전자, LG전자, 다이슨 등 —
   이런 브랜드는 로봇청소기 말고도 파는 게 많아서 이렇게 걸러야 함)
3) 그 외 브랜드는 제품명에 "로봇"이 명시된 경우에만 포함 (안전망)

새로운 브랜드가 계속 나올 수 있으니, 목록에 없어서 빠진 게 있으면 아래
ROBOT_ONLY_BRANDS / GENERIC_BRANDS 리스트에 브랜드명만 추가해주면 됩니다.

사용법:
    python robot_vacuum_monitor.py                  # 실행하면 "원하는 달을 입력하세요"라고 물어봄
                                                      # (8, 9, 12 처럼 월만 입력하면 올해 기준으로 처리,
                                                      #  Enter만 누르면 이번 달로 진행)
    python robot_vacuum_monitor.py --month 2026-09   # 물어보지 않고 바로 특정 월로 실행
    python robot_vacuum_monitor.py --output report.txt
    python robot_vacuum_monitor.py --no-open         # 예약 실행용(메모장 자동 오픈 안 함, 입력받지 않음)
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import ssl
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import json

BASE_URL = "https://hdmzp.github.io/hdhs/homeshopping"
REQUEST_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; RobotVacuumMonitor/1.0)"

COMPANIES = ["HD", "GS", "CJ", "LT"]  # 현대 / GS샵 / CJ온스타일 / 롯데홈쇼핑
KIND_LABEL = {"live": "L", "data": "D"}
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

# --- 로봇청소기 브랜드 판별 목록 (필요하면 여기에 이름만 추가하세요) ---
ROBOT_ONLY_BRANDS = {
    "로보락", "드리미", "존알", "오로와", "나르왈", "에코백스", "타이탈", "아이닉", "유진로봇","roborock","dyson"
}
GENERIC_BRANDS = {
    "삼성전자", "LG전자", "다이슨", "샤오미", "필립스", "테팔", "일렉트로룩스",
}
# 제품명에 "로봇"이 들어가도 로봇청소기가 아닌 것들 (안마의자/헬스로봇/렌탈 등) — 여기에 있으면 무조건 제외
EXCLUDE_KEYWORDS = ["안마의자", "헬스로봇", "렌탈"]
EXCLUDE_CATEGORIES = {"일반렌탈"}

# 회사/기관 네트워크 보안 프록시가 인증서를 가로채는 경우를 자동 우회하기 위한 플래그
INSECURE_SSL = False


def _is_cert_error(err: BaseException) -> bool:
    reason = getattr(err, "reason", None)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return True
    text = str(err)
    return "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text


def _ssl_context() -> Optional[ssl.SSLContext]:
    if not INSECURE_SSL:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_get_json(url: str) -> Optional[dict]:
    """공개 JSON을 가져온다. 존재하지 않으면(404 등) None을 반환한다."""
    global INSECURE_SSL
    req = Request(url, headers={"User-Agent": USER_AGENT})
    tries_left = 3
    while tries_left > 0:
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT, context=_ssl_context()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 404:
                return None
            tries_left -= 1
            if tries_left <= 0:
                print(f"[경고] {url} 요청 실패 (HTTP {e.code})", file=sys.stderr)
                return None
            time.sleep(1)
        except URLError as e:
            if not INSECURE_SSL and _is_cert_error(e):
                INSECURE_SSL = True
                print(
                    "[안내] 이 네트워크의 보안 프로그램(HTTPS 검사 프록시)이 인증서를 가로채는 것으로 보여, "
                    "인증서 검증을 건너뛰고 다시 시도합니다. (내려받는 데이터는 로그인 없는 공개 정보라 안전합니다)",
                    file=sys.stderr,
                )
                continue
            tries_left -= 1
            if tries_left <= 0:
                print(f"[경고] {url} 요청 실패 ({e})", file=sys.stderr)
                return None
            time.sleep(1)
        except (TimeoutError, json.JSONDecodeError) as e:
            tries_left -= 1
            if tries_left <= 0:
                print(f"[경고] {url} 요청 실패 ({e})", file=sys.stderr)
                return None
            time.sleep(1)
    return None


def is_robot_vacuum(brand: str, product: str, category: Optional[str] = None) -> bool:
    brand = (brand or "").strip()
    text = f"{brand} {product}"
    # 안마의자/헬스로봇/렌탈 상품은 "로봇" 단어가 섞여 있어도 로봇청소기가 아니므로 제외
    if any(kw in text for kw in EXCLUDE_KEYWORDS):
        return False
    if category in EXCLUDE_CATEGORIES:
        return False
    if brand in ROBOT_ONLY_BRANDS:
        return True
    if "로봇" in text:
        return True
    return False


# --- 모델명 축약: 알려진 모델 코드 패턴을 우선 추출하고, 없으면 일반 규칙으로 축약 ---
_MODEL_PATTERNS = [
    re.compile(r"S\d{1,2}\s*MaxV(?:\s*Ultra)?", re.IGNORECASE),
    re.compile(r"Q\s?Revo\s?(?:Edge\s?\d+|2\s?Pro|C\s?Pro)", re.IGNORECASE),
    re.compile(r"F25\s*(?:ACE|Ultra|Steam)", re.IGNORECASE),
    re.compile(r"\bH\d{2,3}\b"),
    re.compile(r"X\d\s*Max", re.IGNORECASE),
    re.compile(r"\d+세대"),
]
_BRACKET_RE = re.compile(r"\[[^\]]*\]")
_PAREN_RE = re.compile(r"\([^)]*\)")
_SUFFIX_WORDS = [
    "로봇청소기", "로봇 청소기", "습건식 무선 청소기", "무선 스팀 습건식 청소기",
    "스팀 습건식 청소기", "습건식 청소기", "무선청소기", "물걸레청소기", "청소기",
    "필터", "전용",
]


def extract_model(brand: str, product: str) -> str:
    for pat in _MODEL_PATTERNS:
        m = pat.search(product)
        if m:
            model = re.sub(r"\s+", " ", m.group(0)).strip()
            model = re.sub(r"Q\s?Revo", "Qrevo", model, flags=re.IGNORECASE)
            return model
    s = _BRACKET_RE.sub(" ", product)
    s = _PAREN_RE.sub(" ", s)
    if brand:
        s = s.replace(brand, " ")
    for w in _SUFFIX_WORDS:
        s = s.replace(w, " ")
    s = re.sub(r"\s+", " ", s).strip(" -_/·")
    if not s:
        s = product.strip()
    return s if len(s) <= 22 else s[:22].rstrip() + "…"


@dataclass
class Entry:
    date: str
    weekday: str
    company: str
    kind: str  # "live" / "data"
    start: str
    end: str
    brand: str
    model: str


def collect_month(year_month: str) -> list[Entry]:
    entries: list[Entry] = []
    for company in COMPANIES:
        for kind in ("live", "data"):
            url = f"{BASE_URL}/{company}_{kind}/{year_month}.json"
            data = http_get_json(url)
            if not data:
                continue
            for date_str, items in (data.get("days") or {}).items():
                try:
                    y, m, d = map(int, date_str.split("-"))
                    weekday = WEEKDAY_KO[date(y, m, d).weekday()]
                except ValueError:
                    weekday = ""
                for it in items:
                    brand = (it.get("brand") or "").strip()
                    product = (it.get("product") or "").strip()
                    category = it.get("category")
                    if not product or not is_robot_vacuum(brand, product, category):
                        continue
                    entries.append(Entry(
                        date=date_str, weekday=weekday, company=company, kind=kind,
                        start=it.get("start", ""), end=it.get("end", ""),
                        brand=brand, model=extract_model(brand, product),
                    ))
    entries.sort(key=lambda e: (e.date, e.start))
    return entries


def format_report(year_month: str, entries: list[Entry]) -> str:
    y, m = year_month.split("-")
    lines = [f"■ {y}.{m} 로봇청소기 편성 (HD/GS/CJ/LT · 라이브+디지털방송)"]
    if not entries:
        lines.append("(해당 월에는 아직 로봇청소기 편성 데이터가 없습니다)")
        return "\n".join(lines)

    current_date = None
    for e in entries:
        if e.date != current_date:
            current_date = e.date
            lines.append("")
            lines.append(f"[{e.date}({e.weekday})]")
        kind_label = KIND_LABEL.get(e.kind, e.kind)
        lines.append(f"{e.company}({kind_label}) {e.model} {e.start}")

    # --- 요약표: 채널별 방송 횟수 / 품목별 방송 횟수 ---
    lines.append("")
    lines.append("=" * 32)
    lines.append("■ 채널별 방송 횟수")
    company_counts = Counter(e.company for e in entries)
    for company in COMPANIES:
        count = company_counts.get(company, 0)
        if count:
            lines.append(f"  {company}  {count}건")

    lines.append("")
    lines.append("■ 품목별 방송 횟수")
    model_counts = Counter(e.model for e in entries)
    for model, count in sorted(model_counts.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"  {model}  {count}건")

    return "\n".join(lines)


def prompt_for_month() -> str:
    """대화형 실행(더블클릭 등) 시 조사할 월을 직접 입력받는다.
    "8", "9", "12" 처럼 월만 입력하면 올해 기준으로 처리하고,
    "2026-09" / "2026.09" / "2026/9" 처럼 연도까지 입력해도 인식한다.
    아무것도 입력하지 않고 Enter만 누르면 이번 달로 진행한다."""
    today = date.today()
    default_ym = today.strftime("%Y-%m")
    while True:
        try:
            raw = input(f"원하는 달을 입력하세요 (예: 8, 9, 12 / 그냥 Enter → 이번 달 {default_ym}): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return default_ym
        if not raw:
            return default_ym
        raw = raw.replace(" ", "")
        m = re.match(r"^(\d{4})[-./]?(\d{1,2})$", raw)
        if m:
            y, mo = int(m.group(1)), int(m.group(2))
        elif re.match(r"^\d{1,2}$", raw):
            mo = int(raw)
            y = today.year
        else:
            print("  → 숫자로 입력해주세요 (예: 8, 9, 12). 다시 입력해주세요.")
            continue
        if not (1 <= mo <= 12):
            print("  → 1~12 사이의 월을 입력해주세요.")
            continue
        return f"{y:04d}-{mo:02d}"


def open_in_default_app(path: str) -> None:
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:  # noqa: BLE001
        print(f"[안내] 결과 파일을 자동으로 여는 데 실패했어요. 직접 열어 확인해주세요: {path} ({e})", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="로봇청소기 편성 월간 취합 자동화 (HD/GS/CJ/LT)")
    parser.add_argument("--month", type=str, default=None, help="기준 월 YYYY-MM (기본값: 이번 달)")
    parser.add_argument("--output", type=str, default=None, help="결과를 저장할 txt 파일 경로")
    parser.add_argument("--no-open", action="store_true",
                         help="저장 후 메모장(기본 텍스트 앱)으로 자동으로 열지 않음 (예약 작업 등 무인 실행 시 사용)")
    args = parser.parse_args()

    if args.month:
        year_month = args.month
    elif sys.stdin is not None and sys.stdin.isatty():
        year_month = prompt_for_month()
    else:
        year_month = date.today().strftime("%Y-%m")

    try:
        datetime.strptime(year_month, "%Y-%m")
    except ValueError:
        print(f"[오류] 월 형식이 올바르지 않습니다 (예: 2026-09): {year_month}", file=sys.stderr)
        sys.exit(1)

    entries = collect_month(year_month)
    output_text = format_report(year_month, entries)
    print(output_text)

    out_path = args.output or f"로봇청소기_편성_{year_month.replace('-', '')}.txt"
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output_text + "\n")
        print(f"\n[저장 완료] {out_path} (총 {len(entries)}건)", file=sys.stderr)
        if not args.no_open:
            open_in_default_app(out_path)
    except OSError as e:
        print(f"[경고] 파일 저장 실패: {e}", file=sys.stderr)
        return

    if sys.stdin is None or not sys.stdin.isatty():
        return
    if platform.system() == "Windows":
        try:
            input("\n종료하려면 Enter 키를 누르세요...")
        except (EOFError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    main()