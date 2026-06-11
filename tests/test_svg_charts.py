"""svg_charts.py 单元测试 — 覆盖 bar_chart / line_chart / donut_chart"""
import pytest
from chatlens.plugins.report.svg_charts import bar_chart, line_chart, donut_chart


# ═══════════════════════════════════════════════════════════
#  1. bar_chart()
# ═══════════════════════════════════════════════════════════

class TestBarChart:
    def test_normal_svg(self):
        data = [
            {'label': 'Alice', 'count': 30},
            {'label': 'Bob', 'count': 20},
            {'label': 'Charlie', 'count': 10},
        ]
        svg = bar_chart(data)
        assert '<svg' in svg
        assert '</svg>' in svg
        assert 'Alice' in svg
        assert 'Bob' in svg
        assert '30' in svg
        assert '20' in svg

    def test_empty_data(self):
        svg = bar_chart([])
        assert svg == ''

    def test_single_item(self):
        data = [{'label': 'Only', 'count': 5}]
        svg = bar_chart(data)
        assert '<svg' in svg
        assert 'Only' in svg
        assert '5' in svg

    def test_zero_counts(self):
        data = [
            {'label': 'A', 'count': 0},
            {'label': 'B', 'count': 0},
        ]
        svg = bar_chart(data)
        assert '<svg' in svg

    def test_custom_dimensions(self):
        data = [{'label': 'X', 'count': 10}]
        svg = bar_chart(data, width=800, height=300)
        assert '800' in svg
        assert '300' in svg


# ═══════════════════════════════════════════════════════════
#  2. line_chart()
# ═══════════════════════════════════════════════════════════

class TestLineChart:
    def test_normal_svg(self):
        data = [
            {'label': 'Mon', 'count': 5},
            {'label': 'Tue', 'count': 10},
            {'label': 'Wed', 'count': 8},
        ]
        svg = line_chart(data)
        assert '<svg' in svg
        assert '</svg>' in svg
        assert 'Mon' in svg
        assert 'Tue' in svg
        # 应包含折线路径
        assert '<path' in svg
        assert '<circle' in svg

    def test_empty_data(self):
        svg = line_chart([])
        assert svg == ''

    def test_single_point(self):
        data = [{'label': 'Day1', 'count': 7}]
        svg = line_chart(data)
        assert '<svg' in svg
        assert 'Day1' in svg

    def test_uses_date_label_fallback(self):
        data = [{'date': '2025-01-01', 'count': 3}]
        svg = line_chart(data)
        assert '2025-01-01' in svg

    def test_all_labels_at_14_points(self):
        """数据点 <= 14 时，每个点都画 label。"""
        data = [{'label': f'D{i + 1:02d}', 'count': i + 1} for i in range(14)]
        svg = line_chart(data)
        # 14 个 label 全部应出现
        for i in range(14):
            assert f'D{i + 1:02d}' in svg, f'缺少 label D{i + 1:02d}'
        # 同时应有 14 个数据点圆
        assert svg.count('<circle ') == 14

    def test_thins_labels_at_30_points(self):
        """数据点 > 14 时，label 抽稀到最多 12 个。"""
        data = [{'label': f'D{i + 1:02d}', 'count': (i % 10) + 1} for i in range(30)]
        svg = line_chart(data)
        # 解析所有 <text> 标签内的 label（D01..D30）
        import re
        labels = re.findall(r'<text[^>]*>(D\d{2})</text>', svg)
        # label 数应在 1..12 之间
        assert 1 <= len(labels) <= 12, f'期望 1..12 个 label，实际 {len(labels)}'
        # 30 个数据点圆都应保留
        assert svg.count('<circle ') == 30

    def test_thinned_labels_always_include_endpoints(self):
        """抽稀时首尾两个点必须有 label。"""
        data = [{'label': f'D{i + 1:02d}', 'count': (i % 7) + 1} for i in range(30)]
        svg = line_chart(data)
        assert 'D01' in svg, '起始点 label 必须存在'
        assert 'D30' in svg, '终止点 label 必须存在'


# ═══════════════════════════════════════════════════════════
#  3. donut_chart()
# ═══════════════════════════════════════════════════════════

class TestDonutChart:
    def test_normal_svg(self):
        segments = [
            {'label': 'A', 'count': 40},
            {'label': 'B', 'count': 30},
            {'label': 'C', 'count': 30},
        ]
        svg = donut_chart(segments)
        assert '<svg' in svg
        assert '</svg>' in svg
        # 总数应显示在中心
        assert '100' in svg
        # 应有扇形路径
        assert '<path' in svg
        # 应有中心圆（甜甜圈孔）
        assert '<circle' in svg

    def test_empty_data(self):
        svg = donut_chart([])
        assert svg == ''

    def test_zero_total(self):
        segments = [
            {'label': 'A', 'count': 0},
            {'label': 'B', 'count': 0},
        ]
        svg = donut_chart(segments)
        assert svg == ''

    def test_single_segment(self):
        segments = [{'label': 'Only', 'count': 50}]
        svg = donut_chart(segments)
        assert '<svg' in svg
        assert '50' in svg
        # 单类别应生成完整圆
        assert '<path' in svg

    def test_custom_color(self):
        segments = [
            {'label': 'A', 'count': 10, 'color': '#ff0000'},
            {'label': 'B', 'count': 10},
        ]
        svg = donut_chart(segments)
        assert '#ff0000' in svg

    def test_custom_dimensions(self):
        segments = [{'label': 'X', 'count': 10}]
        svg = donut_chart(segments, width=300, height=300)
        assert '300' in svg
