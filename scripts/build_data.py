#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
가농인증 과수 방제력 앱 — 농약등록정보 수집기
농촌진흥청 농약안전정보시스템(PSIS) OpenAPI → data/*.json

  python3 scripts/build_data.py probe    # 서비스코드·응답필드 확인 (probe.txt 로도 저장)
  python3 scripts/build_data.py build    # data/*.json 생성

인증키는 코드에 넣지 않습니다. 환경변수 PSIS_API_KEY 로만 읽습니다.
표준 라이브러리만 사용하므로 설치할 것이 없습니다.
"""
import os, re, sys, json, time, pathlib, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

# ══════════════════════════════════════════════════════════════════
# [1] 요청 규격 — probe 로 확인 완료
#   service.do?apiKey=…&serviceCode=SVC01&serviceType=AA001
#            &displayCount=100&startPoint=1&cropName=사과
#   · 페이지 넘김은 startPoint 오프셋(1, 101, 201 …)
#   · SVC01 = 농약등록정보 검색 (확인됨)
#   · SVC02 = 존재하나 요청 변수가 다름 (등록취소로 추정) → probe 2부에서 탐색
#   · SVC03~ = 없는 코드
# ══════════════════════════════════════════════════════════════════
BASE       = os.environ.get("PSIS_BASE", "http://psis.rda.go.kr/openApi/service.do")
SVC_REG    = os.environ.get("PSIS_SVC_REGISTER",  "SVC01")
SVC_CANCEL = os.environ.get("PSIS_SVC_CANCELLED", "")   # 확정되면 "SVC02"
CANCEL_ARG = os.environ.get("PSIS_CANCEL_ARGS",   "")   # 예: "searchYear=2026"
SVC_TYPE   = os.environ.get("PSIS_SERVICE_TYPE",  "AA001")
ROWS       = int(os.environ.get("PSIS_ROWS", "100"))
SLEEP      = float(os.environ.get("PSIS_SLEEP", "0.3"))
MAX_PAGES  = int(os.environ.get("PSIS_MAX_PAGES", "800"))

# 응답 필드 이름 — SVC01 실측 기준
FIELD = {
    "상품명":     ["pestiBrandName"],
    "농약한글명": ["pestiKorName"],          # "가스가마이신 액제" (성분 + 제형)
    "영문명":     ["engName"],               # "Kasugamycin SL 2.3 %"
    "용도":       ["useName"],               # "살균" / "살충"
    "작물명":     ["cropName"],
    "병해충":     ["diseaseWeedName"],
    "희석배수":   ["dilutUnit"],             # "1000배 -"
    "잔류기간":   ["useSuittime"],           # "수확14일전"
    "사용횟수":   ["useNum"],                # "3회"
    "사용적기":   ["pestiUse"],              # "꽃이 필 때부터 경엽처리"
    "작용기작":   ["indictSymbl"],           # "라3"
    "회사":       ["compName"],
    "농약코드":   ["pestiCode"],
    # 아래 둘은 SVC01 응답에 없음. data/toxicity.json 이 있으면 채웁니다.
    "인축독성":   ["hazardCode", "toxicGrade", "인축독성"],
    "어독성":     ["fishToxicCode", "어독성"],
}

CROPS = [("apple", "사과"), ("pear", "배"), ("peach", "복숭아"),
         ("plum", "자두"), ("persimmon", "단감")]

# ══════════════════════════════════════════════════════════════════
# [2] 가농 생산규정 필터 (생산규정 3-2 과수 가농인증 방제 규정)
#     · 용도: 살균·살충만  (제초제·호르몬제 제외)
#     · 인축독성 Ⅰ·Ⅱ급 제외 — 단, 등록정보에 독성 항목이 없어
#       값이 있는 경우에만 적용됩니다(없으면 거르지 않고 그대로 둡니다)
#     · 등록취소 품목 제외
#     ※ 발암성은 등록정보에 항목이 없어 자동 판정하지 않습니다.
#     ※ 항생물질계 살균제는 위원회 결정에 따라 별도 판정하지 않습니다.
# ══════════════════════════════════════════════════════════════════
USE_OK = {"살균제", "살충제", "살균·살충제"}
TOX_NG = {"Ⅰ", "Ⅱ"}
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT  = ROOT / "data"


def key():
    k = os.environ.get("PSIS_API_KEY", "").strip()
    if not k:
        sys.exit("PSIS_API_KEY 환경변수가 없습니다. (Actions 에서는 Secrets 로 넣습니다)")
    return k


def safe(x):
    """오류 메시지에 인증키가 섞여 나가지 않도록 가린다(공개 저장소 로그 대비)."""
    t = str(x)
    k = os.environ.get("PSIS_API_KEY", "").strip()
    return t.replace(k, "***") if k else t


def call(service, start=1, rows=ROWS, **search):
    q = {"apiKey": key(), "serviceCode": service, "serviceType": SVC_TYPE,
         "displayCount": rows, "startPoint": start}
    q.update({k: v for k, v in search.items() if v not in (None, "")})
    url = BASE + ("&" if "?" in BASE else "?") + urllib.parse.urlencode(q, encoding="utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": "gano-fruit-pesticide/1.0"})
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read()
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def records(text):
    t = (text or "").lstrip()
    if not t:
        return []
    if t[0] in "{[":
        j = json.loads(t)
        for k in ("body", "items", "item", "list", "data", "response", "result"):
            if isinstance(j, dict) and k in j:
                j = j[k]
        if isinstance(j, dict):
            for v in j.values():
                if isinstance(v, list):
                    j = v
                    break
        return j if isinstance(j, list) else [j]
    root = ET.fromstring(t)
    leaves = [e for e in root.iter() if len(e) and all(len(c) == 0 for c in e)]
    return [{c.tag: (c.text or "").strip() for c in e} for e in leaves]


def err_of(recs):
    """오류 응답이면 (코드, 메시지), 아니면 None"""
    if len(recs) == 1 and "errorCode" in recs[0]:
        return recs[0].get("errorCode", ""), (recs[0].get("errorMsg") or "").split("\n")[0]
    return None


def pick(rec, name):
    for cand in FIELD[name]:
        v = rec.get(cand)
        if v and str(v).strip():
            return str(v).strip()
    return ""


# ── 값 다듬기 ─────────────────────────────────────────────────────
FORM_TAIL = re.compile(r"\s([^\s]*제)$")

def split_kor(name):
    """'가스가마이신 액제' → ('가스가마이신', '액제')"""
    m = FORM_TAIL.search(name or "")
    return ((name[:m.start()].strip(), m.group(1)) if m else ((name or "").strip(), ""))


def dilut(v):
    """'1000배 -' → '1000배'"""
    s = re.sub(r"\s*-\s*$", "", (v or "").strip())
    return re.sub(r"\s+", " ", s)


def content(eng):
    """'Kasugamycin SL 2.3 %' → '2.3%'"""
    m = re.search(r"([\d.]+)\s*%", eng or "")
    return f"{m.group(1)}%" if m else ""


def num(v):
    d = re.search(r"\d+", v or "")
    return int(d.group()) if d else 0


def tox(v):
    s = (v or "").replace(" ", "")
    for w, g in (("맹독", "Ⅰ"), ("고독", "Ⅱ"), ("보통독", "Ⅲ"), ("저독", "Ⅳ")):
        if w in s:
            return g
    for pat, g in (("Ⅳ", "Ⅳ"), ("Ⅲ", "Ⅲ"), ("Ⅱ", "Ⅱ"), ("Ⅰ", "Ⅰ"),
                   ("IV", "Ⅳ"), ("III", "Ⅲ"), ("II", "Ⅱ"),
                   ("4", "Ⅳ"), ("3", "Ⅲ"), ("2", "Ⅱ"), ("1", "Ⅰ")):
        if pat in s:
            return g
    return ""


def use_norm(v):
    s = (v or "").replace(" ", "").replace("·", "")
    if "살균" in s and "살충" in s:
        return "살균·살충제"
    if "살균" in s:
        return "살균제"
    if "살충" in s:
        return "살충제"
    if "제초" in s:
        return "제초제"
    if "생장" in s or "생조" in s:
        return "생장조정제"
    return s or "기타"


def toxicity_table():
    """data/toxicity.json 이 있으면 {성분명: {인축, 어독}} 로 읽는다. 없으면 빈 표."""
    f = OUT / "toxicity.json"
    if not f.exists():
        return {}
    try:
        t = json.loads(f.read_text(encoding="utf-8"))
        return {k: v for k, v in t.items() if isinstance(v, dict)}
    except Exception as e:
        print("  ⚠ toxicity.json 을 읽지 못했습니다:", safe(e))
        return {}


def paginate(service, **search):
    out, start, seen = [], 1, set()
    for _ in range(MAX_PAGES):
        recs = records(call(service, start=start, **search))
        e = err_of(recs)
        if e:
            raise RuntimeError(f"{service} 응답 오류 {e[0]} {e[1]}")
        if not recs:
            break
        sig = json.dumps(recs[0], ensure_ascii=False, sort_keys=True)
        if sig in seen:
            break
        seen.add(sig)
        out += recs
        if len(recs) < ROWS:
            break
        start += ROWS
        time.sleep(SLEEP)
    return out


def cancelled():
    if not SVC_CANCEL:
        print("  ⚠ PSIS_SVC_CANCELLED 가 비어 있어 등록취소 목록을 건너뜁니다")
        return set(), set()
    extra = dict(urllib.parse.parse_qsl(CANCEL_ARG)) if CANCEL_ARG else {}
    try:
        recs = paginate(SVC_CANCEL, **extra)
    except Exception as e:
        sys.exit(f"등록취소 목록 조회 실패: {safe(e)}\n"
                 f"probe 로 요청 변수를 확인한 뒤 PSIS_CANCEL_ARGS 에 넣으십시오.")
    brands = {pick(r, "상품명") for r in recs if pick(r, "상품명")}
    kors   = {pick(r, "농약한글명") for r in recs if pick(r, "농약한글명")}
    print(f"  등록취소 {len(recs)}건 (상표 {len(brands)} / 농약명 {len(kors)})")
    return brands, kors


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    print("등록취소 목록 …")
    dead_brand, dead_kor = cancelled()
    tox_tbl = toxicity_table()
    if tox_tbl:
        print(f"  독성 보완표 {len(tox_tbl)}건 적용")
    results, problems, notes = {}, [], []

    for slug, crop in CROPS:
        try:
            raw = paginate(SVC_REG, cropName=crop)
        except Exception as e:
            problems.append(f"{crop}: {safe(e)}")
            continue
        print(f"{crop}: 원본 {len(raw)}건")
        if not raw:
            problems.append(f"{crop}: 응답 0건")
            continue

        ings, kept, drop = {}, 0, {"용도": 0, "독성": 0, "취소": 0}
        for r in raw:
            c = pick(r, "작물명")
            if c and crop not in c:
                continue
            u = use_norm(pick(r, "용도"))
            if u not in USE_OK:
                drop["용도"] += 1
                continue

            kor = pick(r, "농약한글명")
            ing, form = split_kor(kor)
            brand = pick(r, "상품명") or kor
            if brand in dead_brand or (kor and kor in dead_kor):
                drop["취소"] += 1
                continue

            tv = tox_tbl.get(ing, {})
            t_h = tox(pick(r, "인축독성") or tv.get("인축독성", ""))
            t_f = tox(pick(r, "어독성") or tv.get("어독성", ""))
            if t_h in TOX_NG:                    # 값이 있을 때만 적용
                drop["독성"] += 1
                continue

            item = {
                "상품명": brand, "제형": form, "함량": content(pick(r, "영문명")),
                "회사": pick(r, "회사"), "대상": pick(r, "병해충"),
                "희석배수": dilut(pick(r, "희석배수")),
                "잔류기간": num(pick(r, "잔류기간")), "사용횟수": num(pick(r, "사용횟수")),
                "사용적기": pick(r, "사용적기"), "인축독성": t_h, "어독성": t_f,
            }
            g = ings.setdefault(ing or brand, {"성분명": ing or brand, "용도": u,
                                               "작용기작": pick(r, "작용기작") or "-", "상품": []})
            if item not in g["상품"]:
                g["상품"].append(item)
                kept += 1

        print(f"  → 성분 {len(ings)} / 약제 {kept}   "
              f"(제외: 용도 {drop['용도']}, 독성 {drop['독성']}, 취소 {drop['취소']})")
        if not ings:
            problems.append(f"{crop}: 걸러낸 뒤 0건 — FIELD 매핑 확인 필요")
            continue

        flat = [p for g in ings.values() for p in g["상품"]]
        for name in ("잔류기간", "희석배수", "사용적기"):      # 앱 동작에 필수
            if not any(p.get(name) for p in flat):
                problems.append(f"{crop}: ‘{name}’ 값이 모두 비었습니다 — 필드 매핑 확인 필요")
        for name in ("인축독성", "어독성"):                    # 없어도 진행
            if not any(p.get(name) for p in flat):
                notes.append(f"{crop}: ‘{name}’ 없음 — 앱에서 해당 필터를 자동으로 감춥니다")

        results[slug] = {
            "meta": {
                "작물": crop, "기준일": today,
                "출처": "농촌진흥청 농약안전정보시스템(PSIS) 농약등록정보 검색서비스",
                "샘플": False,
                "필터": "용도 살균·살충 / 등록취소 제외" + (" / 인축독성 Ⅰ·Ⅱ급 제외" if tox_tbl else ""),
                "주의": "인축독성·어독성은 등록정보에 없어 비어 있을 수 있음. 발암성은 자동 판정하지 않음",
            },
            "성분": sorted(ings.values(), key=lambda x: x["성분명"]),
        }

    for n in notes:
        print("  ·", n)
    if problems:
        print("\n── 확인 필요 ──")
        for p in problems:
            print("  ·", p)
    if len(results) < len(CROPS):
        sys.exit("\n작물 일부를 만들지 못했습니다. 기존 data/*.json 을 그대로 둡니다.")

    for slug, doc in results.items():
        (OUT / f"{slug}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    print(f"\n완료 — data/ 5개 파일 갱신 ({today})")


# ── 확인용 ────────────────────────────────────────────────────────
# SVC02 처럼 '파라미터가 잘못되었다'는 응답이 오는 서비스에 대해
# 흔한 요청 변수 조합을 차례로 넣어 보고 통하는 것을 찾습니다.
GUESS = [
    {}, {"searchYear": "2026"}, {"year": "2026"}, {"stdrYear": "2026"},
    {"cancelYear": "2026"}, {"regYear": "2026"},
    {"startDate": "20200101", "endDate": "20261231"},
    {"fromDate": "20200101", "toDate": "20261231"},
    {"bgnDe": "20200101", "endDe": "20261231"},
    {"pestiKorName": "가스가마이신"}, {"pestiBrandName": "가스가민"},
    {"compName": "동방아그로"}, {"cancelDate": "20260101"},
]

def probe():
    lines = []
    def out(s=""):
        print(s); lines.append(s)

    out("── 1부. 서비스코드 확인 ──")
    out(f"{BASE}?apiKey=***&serviceCode=<코드>&serviceType={SVC_TYPE}"
        f"&displayCount=3&startPoint=1&cropName=사과")
    need_args = []
    for code in [f"SVC{i:02d}" for i in range(1, 11)]:
        try:
            recs = records(call(code, start=1, rows=3, cropName="사과"))
        except Exception as e:
            out(f"\n[{code}] 호출 실패: {safe(e)}")
            continue
        e = err_of(recs)
        if e:
            out(f"\n[{code}] {e[0]} — {e[1]}")
            if e[0] != "ERR_103":
                need_args.append(code)
            continue
        if not recs:
            out(f"\n[{code}] 레코드 없음")
            continue
        out(f"\n[{code}] 정상 · 레코드 {len(recs)}건")
        out("  필드: " + ", ".join(recs[0].keys()))
        for k, v in recs[0].items():
            out(f"    {k} = {v}")
        out("  현재 매핑으로 읽은 값:")
        for name in FIELD:
            out(f"    {name}: {pick(recs[0], name) or '(못 찾음)'}")
        time.sleep(SLEEP)

    if need_args:
        out("\n\n── 2부. 요청 변수 찾기 ──")
        out("아래 코드는 존재하지만 요청 변수가 달라 실패했습니다: " + ", ".join(need_args))
    for code in need_args:
        out(f"\n[{code}]")
        for g in GUESS:
            label = ", ".join(f"{k}={v}" for k, v in g.items()) or "(추가 변수 없음)"
            try:
                recs = records(call(code, start=1, rows=3, **g))
            except Exception as ex:
                out(f"  {label} → 호출 실패 {safe(ex)}")
                continue
            e = err_of(recs)
            if e:
                out(f"  {label} → {e[0]}")
            elif recs:
                out(f"  ★ {label} → 성공! 레코드 {len(recs)}건")
                out("     필드: " + ", ".join(recs[0].keys()))
                for k, v in list(recs[0].items())[:20]:
                    out(f"       {k} = {v}")
                break
            else:
                out(f"  {label} → 레코드 없음(오류는 아님)")
            time.sleep(SLEEP)

    (ROOT / "probe.txt").write_text("\n".join(lines), encoding="utf-8")
    out("\n결과를 probe.txt 에도 저장했습니다.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    {"probe": probe, "build": build}.get(cmd, build)()
