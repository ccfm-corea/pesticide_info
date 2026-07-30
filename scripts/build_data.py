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
import os, sys, json, time, pathlib, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

# ══════════════════════════════════════════════════════════════════
# [1] 요청 규격
#     요청 변수 이름은 공개된 호출 예시로 확인된 값입니다.
#     serviceCode 는 probe 로 확인한 뒤 확정하십시오.
#     예) service.do?apiKey=…&serviceCode=SVC01&serviceType=AA001
#         &displayCount=20&startPoint=1&cropName=&pestiBrandName=
#         &diseaseWeedName=&useName=&pestiKorName=&compName=
# ══════════════════════════════════════════════════════════════════
BASE       = os.environ.get("PSIS_BASE", "http://psis.rda.go.kr/openApi/service.do")
SVC_REG    = os.environ.get("PSIS_SVC_REGISTER",  "SVC01")   # 농약등록정보 검색
SVC_CANCEL = os.environ.get("PSIS_SVC_CANCELLED", "")        # 등록취소 농약정보 ← probe 로 확인
SVC_TYPE   = os.environ.get("PSIS_SERVICE_TYPE",  "AA001")
ROWS       = int(os.environ.get("PSIS_ROWS", "100"))
SLEEP      = float(os.environ.get("PSIS_SLEEP", "0.35"))
MAX_PAGES  = int(os.environ.get("PSIS_MAX_PAGES", "600"))

# 응답 필드 이름 후보. 맨 앞에 있는 것부터 찾습니다.
# probe 결과에서 `(못 찾음)`으로 나오는 항목만 고치면 됩니다.
FIELD = {
    "상품명":   ["pestiBrandName", "brandName", "상표명", "품목명"],
    "농약한글명": ["pestiKorName", "농약한글명"],          # 예) "가스가마이신 액제"
    "성분명":   ["ingredientKorName", "mainIngrKorName", "성분명", "주성분"],
    "용도":     ["useName", "용도"],                      # 예) "살균" / "살충"
    "작물명":   ["cropName", "작물명"],
    "병해충":   ["diseaseWeedName", "pestName", "병해충명", "적용병해충"],
    "희석배수": ["dilutUnit", "dilutionRate", "useAmount", "희석배수", "사용량"],
    "잔류기간": ["hvstPrhibitDay", "useSuittime", "수확전일수", "안전사용기준수확전"],
    "사용횟수": ["useNum", "useCount", "사용횟수", "안전사용기준횟수"],
    "사용적기": ["useSuitTimeName", "useTiming", "사용적기", "사용시기"],
    "인축독성": ["hazardCode", "toxicGrade", "poisonCode", "인축독성", "독성"],
    "어독성":   ["fishToxicCode", "fishToxicGrade", "어독성"],
    "제형":     ["formulationName", "제형"],
    "회사":     ["compName", "회사명", "업체명"],
    "작용기작": ["mechanismCode", "작용기작"],
    "등록구분": ["registerType", "구분"],
}

CROPS = [("apple", "사과"), ("pear", "배"), ("peach", "복숭아"),
         ("plum", "자두"), ("persimmon", "단감")]

# ══════════════════════════════════════════════════════════════════
# [2] 가농 생산규정 필터 (생산규정 3-2 과수 가농인증 방제 규정)
#     · 용도: 살균·살충만  (제초제·호르몬제 제외)
#     · 인축독성: Ⅲ·Ⅳ급만 (맹독성 Ⅰ·고독성 Ⅱ 제외)
#     · 등록취소 품목 제외
#     ※ 발암성은 등록정보에 항목이 없어 자동 판정하지 않습니다.
#     ※ 항생물질계 살균제는 위원회 결정에 따라 별도 판정하지 않습니다.
# ══════════════════════════════════════════════════════════════════
USE_OK = {"살균제", "살충제", "살균·살충제"}
TOX_OK = {"Ⅲ", "Ⅳ"}
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT  = ROOT / "data"


def key() -> str:
    k = os.environ.get("PSIS_API_KEY", "").strip()
    if not k:
        sys.exit("PSIS_API_KEY 환경변수가 없습니다. (Actions 에서는 Secrets 로 넣습니다)")
    return k


def call(service, start=1, rows=ROWS, **search) -> str:
    q = {"apiKey": key(), "serviceCode": service, "serviceType": SVC_TYPE,
         "displayCount": rows, "startPoint": start}
    q.update({k: v for k, v in search.items() if v})
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


def records(text: str):
    """XML이든 JSON이든 레코드 목록(dict)으로 만든다."""
    t = text.lstrip()
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


def pick(rec, name) -> str:
    for cand in FIELD[name]:
        v = rec.get(cand)
        if v and str(v).strip():
            return str(v).strip()
    low = {k.lower(): v for k, v in rec.items()}
    for cand in FIELD[name]:
        v = low.get(cand.lower())
        if v and str(v).strip():
            return str(v).strip()
    return ""


def split_kor(name):
    """'가스가마이신 액제' → ('가스가마이신', '액제')"""
    if " " in name:
        head, tail = name.rsplit(" ", 1)
        if tail.endswith("제"):
            return head.strip(), tail.strip()
    return name, ""


def tox(v) -> str:
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


def num(v) -> int:
    d = "".join(c for c in (v or "") if c.isdigit())
    return int(d) if d else 0


def use_norm(v) -> str:
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


def paginate(service, **search):
    """startPoint 기준 오프셋 페이지 넘김."""
    out, start, seen = [], 1, set()
    for _ in range(MAX_PAGES):
        recs = records(call(service, start=start, **search))
        if not recs:
            break
        sig = json.dumps(recs[0], ensure_ascii=False, sort_keys=True)
        if sig in seen:          # 같은 페이지가 되풀이되면 중단
            break
        seen.add(sig)
        out += recs
        if len(recs) < ROWS:
            break
        start += ROWS
        time.sleep(SLEEP)
    return out


# ── 등록취소 ──────────────────────────────────────────────────────
def cancelled():
    if not SVC_CANCEL:
        print("  ⚠ PSIS_SVC_CANCELLED 가 비어 있어 등록취소 목록을 건너뜁니다")
        return set(), set()
    try:
        recs = paginate(SVC_CANCEL)
    except Exception as e:
        sys.exit(f"등록취소 목록 조회 실패: {e}\n서비스코드를 확인하십시오.")
    brands = {pick(r, "상품명") for r in recs if pick(r, "상품명")}
    kors   = {pick(r, "농약한글명") for r in recs if pick(r, "농약한글명")}
    print(f"  등록취소 {len(recs)}건 (상표 {len(brands)} / 농약명 {len(kors)})")
    return brands, kors


# ── 등록정보 ──────────────────────────────────────────────────────
def build():
    OUT.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    print("등록취소 목록 …")
    dead_brand, dead_kor = cancelled()
    results, problems = {}, []

    for slug, crop in CROPS:
        raw = paginate(SVC_REG, cropName=crop)
        print(f"{crop}: 원본 {len(raw)}건")
        if not raw:
            problems.append(f"{crop}: 응답 0건")
            continue

        ings, kept, dropped = {}, 0, {"용도": 0, "독성": 0, "취소": 0}
        for r in raw:
            c = pick(r, "작물명")
            if c and crop not in c:
                continue
            u = use_norm(pick(r, "용도"))
            if u not in USE_OK:
                dropped["용도"] += 1
                continue
            t = tox(pick(r, "인축독성"))
            if t and t not in TOX_OK:
                dropped["독성"] += 1
                continue

            brand = pick(r, "상품명")
            kor   = pick(r, "농약한글명")
            ing   = pick(r, "성분명") or split_kor(kor)[0] or brand
            form  = pick(r, "제형") or split_kor(kor)[1]
            if brand in dead_brand or (kor and kor in dead_kor):
                dropped["취소"] += 1
                continue

            item = {
                "상품명": brand or kor, "제형": form, "회사": pick(r, "회사"),
                "대상": pick(r, "병해충"), "희석배수": pick(r, "희석배수"),
                "잔류기간": num(pick(r, "잔류기간")), "사용횟수": num(pick(r, "사용횟수")),
                "사용적기": pick(r, "사용적기"), "인축독성": t, "어독성": tox(pick(r, "어독성")),
            }
            g = ings.setdefault(ing, {"성분명": ing, "용도": u,
                                      "작용기작": pick(r, "작용기작") or "-", "상품": []})
            if item not in g["상품"]:
                g["상품"].append(item)
                kept += 1

        print(f"  → 성분 {len(ings)} / 약제 {kept}   "
              f"(제외: 용도 {dropped['용도']}, 독성 {dropped['독성']}, 취소 {dropped['취소']})")
        if not ings:
            problems.append(f"{crop}: 걸러낸 뒤 0건 — FIELD 매핑을 확인하십시오")
            continue

        # 핵심 항목이 비어 있으면 알린다 (앱의 잔류기간 계산이 여기에 달려 있음)
        flat = [p for g in ings.values() for p in g["상품"]]
        for name in ("잔류기간", "인축독성", "희석배수", "사용적기"):
            filled = sum(1 for p in flat if p.get(name))
            if filled == 0:
                problems.append(f"{crop}: ‘{name}’ 값이 모두 비었습니다 — 필드 매핑 또는 서비스 확인 필요")

        results[slug] = {
            "meta": {
                "작물": crop, "기준일": today,
                "출처": "농촌진흥청 농약안전정보시스템(PSIS) 농약등록정보 검색서비스",
                "샘플": False,
                "필터": "용도 살균·살충 / 인축독성 Ⅲ·Ⅳ급 / 등록취소 제외",
                "주의": "발암성 여부는 원 데이터에 없어 자동 판정하지 않음",
            },
            "성분": sorted(ings.values(), key=lambda x: x["성분명"]),
        }

    if problems:
        print("\n── 확인 필요 ──")
        for p in problems:
            print("  ·", p)
    if len(results) < len(CROPS):
        sys.exit("\n작물 일부를 만들지 못했습니다. 기존 data/*.json 을 그대로 둡니다.")

    for slug, doc in results.items():   # 전부 성공했을 때만 덮어씀
        (OUT / f"{slug}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    print(f"\n완료 — data/ 5개 파일 갱신 ({today})")


# ── 확인용 ────────────────────────────────────────────────────────
def probe():
    lines = []
    def out(s=""):
        print(s); lines.append(s)

    out("── 요청 규격 ──")
    out(f"{BASE}?apiKey=***&serviceCode=<코드>&serviceType={SVC_TYPE}"
        f"&displayCount=3&startPoint=1&cropName=사과")
    for code in [f"SVC{i:02d}" for i in range(1, 11)]:
        try:
            text = call(code, start=1, rows=3, cropName="사과")
        except Exception as e:
            out(f"\n[{code}] 호출 실패: {e}")
            continue
        try:
            recs = records(text)
        except Exception as e:
            out(f"\n[{code}] 파싱 실패: {e} / 원문 앞부분: {text[:160]}")
            continue
        if not recs:
            try:
                recs = records(call(code, start=1, rows=3))
            except Exception:
                recs = []
        if not recs:
            out(f"\n[{code}] 레코드 없음 / 원문 앞부분: {text[:160]}")
            continue
        out(f"\n[{code}] 레코드 {len(recs)}건")
        out("  필드: " + ", ".join(recs[0].keys()))
        out("  첫 레코드:")
        for k, v in recs[0].items():
            out(f"    {k} = {v}")
        out("  현재 매핑으로 읽은 값:")
        for name in FIELD:
            out(f"    {name}: {pick(recs[0], name) or '(못 찾음)'}")
        time.sleep(SLEEP)

    (ROOT / "probe.txt").write_text("\n".join(lines), encoding="utf-8")
    out("\n결과를 probe.txt 에도 저장했습니다. 이 파일 내용을 그대로 공유하면 됩니다.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    {"probe": probe, "build": build}.get(cmd, build)()
