"""Prometheus 兼容的指标模块（无外部依赖）。

G4-2.1: Prometheus /metrics 端点 + middleware

设计目标
--------
- 零外部依赖：不引入 ``prometheus_client`` pip 包（保持 requirements.txt 干净）
- 文本格式严格遵守 Prometheus 0.0.4 (text/plain)
- 线程安全：``Counter`` / ``Histogram`` / ``Gauge`` 内部用 ``threading.Lock`` 保护
- 全局单例：``MetricsRegistry`` 进程内一份，业务代码 / middleware 共享

公开指标
--------
HTTP
    - http_requests_total{method,path,status}  counter
    - http_request_duration_seconds{...}       histogram

业务
    - ide_tasks_active                          gauge
    - ide_tasks_total{group,fmt}               counter
    - reports_generated_total{fmt,status}       counter
    - errors_total{code}                        counter
    - db_connections_active                     gauge
    - wal_size_bytes                            gauge
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Iterable


# ── 默认 Histogram buckets（Prometheus 官方推荐） ─────────────────────
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


# ── Counter ──────────────────────────────────────────────────────────
class Counter:
    """线程安全 Counter：单调递增计数器，支持 labels。"""

    def __init__(
        self,
        name: str,
        help_text: str,
        labelnames: Iterable[str] = (),
    ) -> None:
        self.name = name
        self.help = help_text
        self.labelnames: tuple[str, ...] = tuple(labelnames)
        # defaultdict(float) 在 ``inc`` 第一次写入时自动 0.0
        self._values: dict[tuple[str, ...], float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        """累加 amount（默认 1.0）。labels 必须覆盖所有 labelnames（多则忽略缺省值）。"""
        key = tuple(labels.get(n, "") for n in self.labelnames)
        with self._lock:
            self._values[key] += amount

    def get(self, **labels: str) -> float:
        """读取当前值（测试用）。"""
        key = tuple(labels.get(n, "") for n in self.labelnames)
        with self._lock:
            return self._values.get(key, 0.0)


# ── Histogram ────────────────────────────────────────────────────────
class Histogram:
    """线程安全 Histogram：累积观测值，按 buckets 统计计数。"""

    def __init__(
        self,
        name: str,
        help_text: str,
        labelnames: Iterable[str] = (),
        buckets: Iterable[float] = DEFAULT_BUCKETS,
    ) -> None:
        self.name = name
        self.help = help_text
        self.labelnames = tuple(labelnames)
        # 升序排列的桶边界
        self.buckets: tuple[float, ...] = tuple(sorted(buckets))
        # 每个 label-set 独立维护观测列表（O(N) 桶内计数，简单可靠）
        self._observations: dict[tuple[str, ...], list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        """记录一次观测值。"""
        key = tuple(labels.get(n, "") for n in self.labelnames)
        with self._lock:
            self._observations[key].append(value)

    def _bucket_counts(self, observations: list[float]) -> list[tuple[float, int]]:
        """返回 [(bucket_upper, count), ...]，按 bucket 升序。"""
        # 排序后用 bisect 加速（O(N log N) 总开销）；N 通常 < 1e4 可接受
        sorted_obs = sorted(observations)
        result: list[tuple[float, int]] = []
        for bucket in self.buckets:
            # bisect_right 找 ``<= bucket`` 的最大下标 + 1 = 计数
            import bisect

            count = bisect.bisect_right(sorted_obs, bucket)
            result.append((bucket, count))
        return result

    def get_observations(self, **labels: str) -> list[float]:
        with self._lock:
            return list(self._observations.get(tuple(labels.get(n, "") for n in self.labelnames), []))


# ── Gauge ────────────────────────────────────────────────────────────
class Gauge:
    """线程安全 Gauge：瞬时值。"""

    def __init__(
        self,
        name: str,
        help_text: str,
        labelnames: Iterable[str] = (),
    ) -> None:
        self.name = name
        self.help = help_text
        self.labelnames = tuple(labelnames)
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def set(self, value: float, **labels: str) -> None:
        """设置瞬时值。"""
        key = tuple(labels.get(n, "") for n in self.labelnames)
        with self._lock:
            self._values[key] = value

    def get(self, **labels: str) -> float:
        with self._lock:
            return self._values.get(tuple(labels.get(n, "") for n in self.labelnames), 0.0)


# ── 工具：构造 label 字符串 ──────────────────────────────────────
def _render_labels(labelnames: tuple[str, ...], key: tuple[str, ...]) -> str:
    """拼接 ``name="value",name2="value2"`` 串（空值标签会被省略以节省字节）。"""
    parts: list[str] = []
    for n, v in zip(labelnames, key):
        if v == "":
            continue
        # 转义反斜杠 / 双引号 / 换行
        v_esc = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        parts.append(f'{n}="{v_esc}"')
    return ",".join(parts)


# ── MetricsRegistry ──────────────────────────────────────────────
class MetricsRegistry:
    """进程内全局单例：容纳所有 Counter / Histogram / Gauge。"""

    _instance: "MetricsRegistry | None" = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "MetricsRegistry":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> "MetricsRegistry":
        """测试辅助：清空单例（强制下次 instance() 重建）。"""
        with cls._instance_lock:
            cls._instance = None
        return cls.instance()

    def __init__(self) -> None:
        # ── HTTP ──
        self.http_requests_total = Counter(
            "http_requests_total",
            "HTTP 请求总数",
            labelnames=("method", "path", "status"),
        )
        self.http_request_duration_seconds = Histogram(
            "http_request_duration_seconds",
            "HTTP 请求延迟（秒）",
            labelnames=("method", "path", "status"),
        )

        # ── 业务 ──
        self.ide_tasks_active = Gauge(
            "ide_tasks_active",
            "当前活跃 IDE 任务数（in-flight 线程集合大小）",
        )
        self.ide_tasks_total = Counter(
            "ide_tasks_total",
            "IDE 任务创建总数",
            labelnames=("group", "fmt"),
        )
        self.reports_generated_total = Counter(
            "reports_generated_total",
            "报告生成总数",
            labelnames=("fmt", "status"),
        )
        self.errors_total = Counter(
            "errors_total",
            "错误总数",
            labelnames=("code",),
        )
        self.db_connections_active = Gauge(
            "db_connections_active",
            "活跃 SQLite 连接数",
        )
        self.wal_size_bytes = Gauge(
            "wal_size_bytes",
            "WAL 文件大小（字节）",
        )

    def render(self) -> str:
        """渲染为 Prometheus 文本格式 0.0.4。"""
        lines: list[str] = []

        # ── Counter ──
        for c in (
            self.http_requests_total,
            self.ide_tasks_total,
            self.reports_generated_total,
            self.errors_total,
        ):
            lines.append(f"# HELP {c.name} {c.help}")
            lines.append(f"# TYPE {c.name} counter")
            with c._lock:
                items = list(c._values.items())
            for key, value in items:
                labels = _render_labels(c.labelnames, key)
                if labels:
                    lines.append(f"{c.name}{{{labels}}} {value}")
                else:
                    lines.append(f"{c.name} {value}")

        # ── Histogram ──
        for h in (self.http_request_duration_seconds,):
            lines.append(f"# HELP {h.name} {h.help}")
            lines.append(f"# TYPE {h.name} histogram")
            with h._lock:
                obs_items = list(h._observations.items())
            for key, observations in obs_items:
                labels = _render_labels(h.labelnames, key)
                bucket_counts = h._bucket_counts(observations)
                cumulative = 0
                for bucket, count in bucket_counts:
                    cumulative += count
                    b_label = f'le="{bucket}"'
                    full_label = f"{labels},{b_label}" if labels else b_label
                    lines.append(f"{h.name}_bucket{{{full_label}}} {cumulative}")
                # +Inf bucket = 全部观测数
                inf_label = 'le="+Inf"'
                full_inf = f"{labels},{inf_label}" if labels else inf_label
                lines.append(f"{h.name}_bucket{{{full_inf}}} {len(observations)}")
                # _sum / _count
                total = sum(observations)
                if labels:
                    lines.append(f"{h.name}_sum{{{labels}}} {total}")
                    lines.append(f"{h.name}_count{{{labels}}} {len(observations)}")
                else:
                    lines.append(f"{h.name}_sum {total}")
                    lines.append(f"{h.name}_count {len(observations)}")

        # ── Gauge ──
        for g in (
            self.ide_tasks_active,
            self.db_connections_active,
            self.wal_size_bytes,
        ):
            lines.append(f"# HELP {g.name} {g.help}")
            lines.append(f"# TYPE {g.name} gauge")
            with g._lock:
                items = list(g._values.items())
            for key, value in items:
                labels = _render_labels(g.labelnames, key)
                if labels:
                    lines.append(f"{g.name}{{{labels}}} {value}")
                else:
                    lines.append(f"{g.name} {value}")

        # Prometheus 要求行尾以 \n 结束
        return "\n".join(lines) + "\n"


# 模块级单例：业务代码 / middleware 共享
REGISTRY: MetricsRegistry = MetricsRegistry.instance()


__all__ = [
    "Counter",
    "Histogram",
    "Gauge",
    "MetricsRegistry",
    "REGISTRY",
    "DEFAULT_BUCKETS",
]
