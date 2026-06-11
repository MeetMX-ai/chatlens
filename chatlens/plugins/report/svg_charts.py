import html
import math
from typing import List, Dict


def _esc(text: str) -> str:
    return html.escape(str(text))


def bar_chart(
    data: List[Dict], width: int = 640, height: int = 200, bar_color: str = "#e07850"
) -> str:
    if not data:
        return ""
    max_val = max(d["count"] for d in data) or 1
    bar_w = max(8, min(24, (width - 40) // len(data) - 4))
    chart_h = height - 30
    parts = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
    ]
    for i, d in enumerate(data):
        x = 20 + i * (bar_w + 4)
        h = int(d["count"] / max_val * (chart_h - 10))
        y = chart_h - h
        ratio = d["count"] / max_val
        color = bar_color if ratio > 0.6 else "#d4a853" if ratio > 0.3 else "#c8c4bc"
        parts.append(
            f'<rect x="{x}" y="{y}" width="{bar_w}" height="{h}" rx="3" fill="{color}"/>'
        )
        label = _esc(d.get("label", str(i)))
        parts.append(
            f'<text x="{x + bar_w // 2}" y="{chart_h + 14}" text-anchor="middle" font-size="9" fill="#9e9689" font-family="sans-serif">{label}</text>'
        )
        if d["count"] > 0:
            parts.append(
                f'<text x="{x + bar_w // 2}" y="{y - 4}" text-anchor="middle" font-size="8" fill="#6b6560" font-family="sans-serif">{d["count"]}</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)


def line_chart(
    data: List[Dict], width: int = 640, height: int = 200, line_color: str = "#e07850"
) -> str:
    if not data:
        return ""
    max_val = max(d["count"] for d in data) or 1
    chart_h = height - 30
    chart_w = width - 40
    points = []
    for i, d in enumerate(data):
        x = 20 + int(i / max(len(data) - 1, 1) * chart_w)
        y = chart_h - int(d["count"] / max_val * (chart_h - 10))
        points.append((x, y))
    parts = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
    ]
    fill_path = (
        f"M {points[0][0]},{chart_h} "
        + " ".join(f"L {x},{y}" for x, y in points)
        + f" L {points[-1][0]},{chart_h} Z"
    )
    parts.append(f'<path d="{fill_path}" fill="{line_color}10" stroke="none"/>')
    line_path = "M " + " ".join(f"{x},{y}" for x, y in points)
    parts.append(
        f'<path d="{line_path}" fill="none" stroke="{line_color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'
    )
    for x, y in points:
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="3.5" fill="{line_color}" stroke="#fff" stroke-width="1.5"/>'
        )
    # 仅在点数 <= 14 时全画 label，否则抽稀到最多 12 个（首尾必画）
    if len(data) <= 14:
        label_indices = set(range(len(data)))
    else:
        n = len(data)
        # 在 [0, n-1] 上等距取 12 个点，保证首尾 + 中间 10 个等距
        label_indices = {
            int(round(i * (n - 1) / 11)) for i in range(12)
        }
        label_indices.add(0)
        label_indices.add(n - 1)
    for i, d in enumerate(data):
        if i not in label_indices:
            continue
        x = 20 + int(i / max(len(data) - 1, 1) * chart_w)
        label = _esc(d.get("label", d.get("date", str(i))))
        parts.append(
            f'<text x="{x}" y="{chart_h + 14}" text-anchor="middle" font-size="9" fill="#9e9689" font-family="sans-serif">{label}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def donut_chart(segments: List[Dict], width: int = 200, height: int = 200) -> str:
    if not segments:
        return ""
    total = sum(s.get("count", 0) for s in segments)
    if total == 0:
        return ""
    cx, cy, r = width // 2, height // 2, min(width, height) // 2 - 10
    colors = [
        "#e07850",
        "#d4a853",
        "#4a9b8c",
        "#8b6bb3",
        "#5b8cd0",
        "#ec4899",
        "#10b981",
        "#f59e0b",
    ]
    parts = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
    ]
    start_angle = 0
    for i, seg in enumerate(segments):
        pct = seg.get("count", 0) / total
        angle = pct * 360
        end_angle = start_angle + angle
        large_arc = 1 if angle > 180 else 0
        x1 = cx + r * math.cos(math.radians(start_angle - 90))
        y1 = cy + r * math.sin(math.radians(start_angle - 90))
        x2 = cx + r * math.cos(math.radians(end_angle - 90))
        y2 = cy + r * math.sin(math.radians(end_angle - 90))
        color = seg.get("color") or colors[i % len(colors)]
        if pct > 0.001:
            parts.append(
                f'<path d="M {cx},{cy} L {x1:.1f},{y1:.1f} A {r},{r} 0 {large_arc} 1 {x2:.1f},{y2:.1f} Z" fill="{color}"/>'
            )
        start_angle = end_angle
    inner_r = int(r * 0.55)
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{inner_r}" fill="white"/>')
    parts.append(
        f'<text x="{cx}" y="{cy + 5}" text-anchor="middle" font-size="14" font-weight="bold" fill="#2d2a26" font-family="sans-serif">{total}</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def progress_bar(
    percentage: float, width: int = 400, height: int = 10, color: str = "#e07850"
) -> str:
    w = int(percentage / 100 * width)
    return f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg"><rect width="{width}" height="{height}" rx="{height // 2}" fill="#ebe6de"/><rect width="{w}" height="{height}" rx="{height // 2}" fill="{color}"/></svg>'
