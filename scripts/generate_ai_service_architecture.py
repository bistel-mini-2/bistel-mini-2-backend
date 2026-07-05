from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

W, H = 2400, 1500
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "architecture"
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"

C = {
    "bg": "#FFF9F3", "ink": "#403836", "muted": "#796D69",
    "coral": "#D94F5C", "hot": "#F36B72", "salmon": "#FF8C7A",
    "apricot": "#FFB38A", "peach": "#FFD4BE", "blush": "#FFE4E8",
    "cream": "#FFF1E7", "white": "#FFFFFF", "line": "#D9826B",
    "pale": "#FFFDFB", "green": "#B7D8C0",
}


@dataclass(frozen=True)
class Box:
    id: str
    xy: tuple[int, int, int, int]
    title: str
    subtitle: str = ""
    items: tuple[str, ...] = ()
    fill: str = C["white"]
    accent: str = C["coral"]


BOXES = [
    Box("user", (70, 260, 340, 670), "사용자", "정책 탐색과 AI 요청", ("맞춤 추천", "지원 가능성", "정책 비교", "AI 채팅"), C["blush"]),
    Box("front", (390, 260, 730, 670), "Frontend", "Next.js 화면 · API Client", ("Recommend / Eligibility", "Compare / Policy", "Chat", "SSE · Polling"), C["cream"], C["salmon"]),
    Box("api", (780, 260, 1120, 670), "FastAPI", "API · Service Boundary", ("Controllers · Schemas", "Services · Repositories", "Auth / JWT", "Request Lifecycle"), C["peach"], C["salmon"]),
    Box("chat", (1170, 245, 1710, 455), "Chat Orchestration", "Python Handler 기반 직접 라우팅", ("ChatService", "Intent Classifier → Handler Router"), C["hot"], C["white"]),
    Box("state", (1170, 485, 1710, 670), "Conversation State", "의도보다 진행 상태를 먼저 처리", ("프로필 확인", "필수 조건 수집", "Follow-Up · slot_json"), C["blush"]),
    Box("openai", (1770, 260, 2325, 670), "OpenAI", "구조화 출력 · 생성 · 임베딩", ("gpt-5.4-mini", "gpt-4o · Vision", "text-embedding-3-large"), C["cream"], C["coral"]),
    Box("recommend", (410, 790, 820, 1015), "Recommendation Graph", "맞춤 정책 추천", ("조건 추출 · 프로필 병합", "규칙 필터 · 후보 생성", "LLM 재정렬"), C["blush"]),
    Box("eligibility", (850, 790, 1260, 1015), "Eligibility Graph", "정책별 지원 가능성", ("조건·근거 수집", "규칙 판정 + LLM 보조", "추가 질문 생성"), C["peach"]),
    Box("compare", (1290, 790, 1700, 1015), "Comparison Graph", "정책 비교", ("비교 대상 해석", "항목 정규화", "비교 가이드 생성"), C["cream"]),
    Box("summary", (1730, 790, 2140, 1015), "Policy Summary Graph", "정책 핵심 요약", ("정책 chunk 검색", "근거 기반 요약", "정책 링크 연결"), C["blush"]),
    Box("ingest", (70, 1110, 530, 1400), "Policy Data Ingestion", "사전 데이터 구축 · Offline", ("공공데이터 · 복지로", "PDF / HTML 원문 수집", "텍스트 추출 · chunk", "Vision 보완 · embedding"), C["pale"], C["salmon"]),
    Box("shared", (570, 1110, 1370, 1400), "Shared AI Capabilities", "모든 워크플로우가 공유", ("RAG Search", "Rule Filter", "LLM Judgement · Rerank", "Evidence · Quality Validation"), C["apricot"], C["coral"]),
    Box("db", (1410, 1110, 1885, 1400), "PostgreSQL + pgvector", "업무 데이터 · Vector Search", ("정책 · 사용자 · 가족 프로필", "대화 · 요청 · slot_json", "추천 후보 · assessment", "chunk embedding · evidence"), C["peach"]),
    Box("response", (1925, 1110, 2325, 1400), "Response & Persistence", "저장 완료 후 화면 반환", ("구조화 응답 · 정책 카드", "근거 · 링크", "SSE done · Polling", "failed · recovery"), C["blush"]),
]

EDGES = [
    ("user", "front", "입력", "runtime"), ("front", "api", "API 요청", "runtime"),
    ("api", "chat", "채팅 요청", "runtime"), ("chat", "state", "상태 우선", "runtime"),
    ("chat", "openai", "의도 분류 · 생성", "runtime"),
    ("api", "recommend", "기능별 실행", "runtime"),
    ("api", "eligibility", "", "runtime"),
    ("api", "compare", "", "runtime"),
    ("api", "summary", "", "runtime"),
    ("openai", "summary", "모델 호출", "runtime"),
    ("ingest", "db", "chunk · embedding", "offline"), ("shared", "db", "검색 · 저장", "persist"),
    ("db", "response", "결과 조회", "persist"),
]


def center(box: Box) -> tuple[int, int]:
    x1, y1, x2, y2 = box.xy
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def edge_points(a: Box, b: Box) -> list[tuple[int, int]]:
    ax, ay = center(a); bx, by = center(b)
    if bx > ax + 80: start, end = (a.xy[2], ay), (b.xy[0], by)
    elif bx < ax - 80: start, end = (a.xy[0], ay), (b.xy[2], by)
    elif by > ay: start, end = (ax, a.xy[3]), (bx, b.xy[1])
    else: start, end = (ax, a.xy[1]), (bx, b.xy[3])
    if start[0] == end[0] or start[1] == end[1]: return [start, end]
    mid = (start[0] + end[0]) // 2
    return [start, (mid, start[1]), (mid, end[1]), end]


def svg_text(x: int, y: int, text: str, size: int, color: str, weight: int = 400, anchor: str = "start") -> str:
    return f'<text x="{x}" y="{y}" font-family="Apple SD Gothic Neo, sans-serif" font-size="{size}" font-weight="{weight}" fill="{color}" text-anchor="{anchor}">{escape(text)}</text>'


def build_svg() -> str:
    by_id = {b.id: b for b in BOXES}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
           '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#D9826B"/></marker></defs>',
           f'<rect width="{W}" height="{H}" fill="{C["bg"]}"/>']
    out += [svg_text(70, 82, "ARCHITECTURE", 24, C["coral"], 700), svg_text(70, 150, "전체 AI 서비스 아키텍처", 54, C["ink"], 700),
            svg_text(70, 202, "정책 데이터 구축부터 실시간 AI 실행, 근거 기반 응답과 저장까지", 27, C["muted"]),
            f'<rect x="1510" y="92" width="815" height="74" rx="37" fill="{C["hot"]}"/>',
            svg_text(1917, 140, "Offline Data  →  Runtime AI  →  Evidence & Persistence", 23, C["white"], 700, "middle")]
    for s, t, label, kind in EDGES:
        pts = edge_points(by_id[s], by_id[t]); path = " ".join(("M" if i == 0 else "L") + f" {x} {y}" for i, (x, y) in enumerate(pts))
        dash = ' stroke-dasharray="12 10"' if kind == "offline" else ""; width = 5 if kind == "persist" else 3
        out.append(f'<path d="{path}" fill="none" stroke="{C["line"]}" stroke-width="{width}"{dash} marker-end="url(#arrow)" opacity="0.85"/>')
        if label:
            mx, my = pts[len(pts)//2]; out.append(svg_text(mx, my-10, label, 18, C["muted"], 600, "middle"))
    out.append(f'<rect x="370" y="735" width="1790" height="325" rx="34" fill="none" stroke="{C["coral"]}" stroke-width="3" stroke-dasharray="10 8"/>')
    out.append(svg_text(400, 775, "INDEPENDENT LANGGRAPH WORKFLOWS", 20, C["coral"], 700))
    for b in BOXES:
        x1,y1,x2,y2=b.xy; out.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" rx="28" fill="{b.fill}" stroke="#F2C9BA" stroke-width="2"/>')
        out.append(f'<rect x="{x1}" y="{y1}" width="9" height="{y2-y1}" rx="5" fill="{b.accent}"/>')
        color = C["white"] if b.fill == C["hot"] else C["ink"]
        out.append(svg_text(x1+34,y1+52,b.title,29,color,700)); yy=y1+87
        if b.subtitle: out.append(svg_text(x1+34,yy,b.subtitle,20,color if b.fill==C["hot"] else C["muted"],500)); yy+=42
        for item in b.items:
            out.append(svg_text(x1+38,yy,"• "+item,19,color,500)); yy+=34
    out.append('</svg>')
    return "\n".join(out)


def font(size: int, bold: bool = False):
    return ImageFont.truetype(FONT, size=size, index=1 if bold else 0)


def draw_arrow(draw: ImageDraw.ImageDraw, pts: list[tuple[int,int]], kind: str, label: str):
    width=5 if kind=="persist" else 3
    if kind=="offline":
        for a,b in zip(pts,pts[1:]):
            steps=max(1,int(((b[0]-a[0])**2+(b[1]-a[1])**2)**.5//18))
            for i in range(0,steps,2):
                p=(a[0]+(b[0]-a[0])*i/steps,a[1]+(b[1]-a[1])*i/steps); q=(a[0]+(b[0]-a[0])*min(i+1,steps)/steps,a[1]+(b[1]-a[1])*min(i+1,steps)/steps); draw.line([p,q],fill=C["line"],width=width)
    else: draw.line(pts,fill=C["line"],width=width,joint="curve")
    p,q=pts[-2],pts[-1]; import math
    ang=math.atan2(q[1]-p[1],q[0]-p[0]); r=14
    tri=[q,(q[0]-r*math.cos(ang-.55),q[1]-r*math.sin(ang-.55)),(q[0]-r*math.cos(ang+.55),q[1]-r*math.sin(ang+.55))]
    draw.polygon(tri,fill=C["line"])
    if label:
        mx,my=pts[len(pts)//2]; draw.text((mx,my-26),label,font=font(18,True),fill=C["muted"],anchor="mm",stroke_width=4,stroke_fill=C["bg"])


def build_png(path: Path):
    im=Image.new("RGB",(W,H),C["bg"]); d=ImageDraw.Draw(im)
    d.text((70,62),"ARCHITECTURE",font=font(24,True),fill=C["coral"]); d.text((70,105),"전체 AI 서비스 아키텍처",font=font(54,True),fill=C["ink"])
    d.text((70,178),"정책 데이터 구축부터 실시간 AI 실행, 근거 기반 응답과 저장까지",font=font(27),fill=C["muted"])
    d.rounded_rectangle((1510,92,2325,166),37,fill=C["hot"]); d.text((1917,130),"Offline Data  →  Runtime AI  →  Evidence & Persistence",font=font(23,True),fill=C["white"],anchor="mm")
    ids={b.id:b for b in BOXES}
    for s,t,l,k in EDGES: draw_arrow(d,edge_points(ids[s],ids[t]),k,l)
    d.rounded_rectangle((370,735,2160,1060),34,outline=C["coral"],width=3); d.text((400,748),"INDEPENDENT LANGGRAPH WORKFLOWS",font=font(20,True),fill=C["coral"])
    for b in BOXES:
        x1,y1,x2,y2=b.xy; d.rounded_rectangle(b.xy,28,fill=b.fill,outline="#F2C9BA",width=2); d.rounded_rectangle((x1,y1,x1+9,y2),5,fill=b.accent)
        col=C["white"] if b.fill==C["hot"] else C["ink"]; d.text((x1+34,y1+24),b.title,font=font(29,True),fill=col); yy=y1+70
        if b.subtitle: d.text((x1+34,yy),b.subtitle,font=font(20),fill=col if b.fill==C["hot"] else C["muted"]); yy+=42
        for item in b.items: d.text((x1+38,yy),"• "+item,font=font(19),fill=col); yy+=34
    im.save(path,optimize=True)


def main():
    OUT.mkdir(parents=True,exist_ok=True); svg=OUT/"ai-service-architecture.svg"; png=OUT/"ai-service-architecture.png"
    svg.write_text(build_svg(),encoding="utf-8"); build_png(png)
    print(svg); print(png)


if __name__ == "__main__": main()
