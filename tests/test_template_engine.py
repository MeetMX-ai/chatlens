"""template_engine.py 单元测试 — _get_env、render、list_themes、模板缓存"""

import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from chatlens.plugins.report import template_engine


# ── _get_env (初始化/模板加载/缓存) ───────────────────────────

class TestGetEnv:
    def setup_method(self):
        """每个测试前清空缓存"""
        template_engine._env_cache.clear()

    def test_returns_environment_for_valid_theme(self):
        """有效主题应返回 Jinja2 Environment"""
        env = template_engine._get_env('classic')
        assert env is not None
        from jinja2 import Environment
        assert isinstance(env, Environment)

    def test_caches_environment(self):
        """同一主题应返回缓存的 Environment 实例"""
        env1 = template_engine._get_env('classic')
        env2 = template_engine._get_env('classic')
        assert env1 is env2

    def test_fallback_to_classic_for_invalid_theme(self):
        """无效主题应回退到 classic"""
        env = template_engine._get_env('nonexistent_theme_xyz')
        assert env is not None
        # 回退到 classic 后应能正常工作
        # 验证能加载 classic 的模板
        template = env.get_template('report.html')
        assert template is not None

    def test_different_themes_return_different_envs(self):
        """不同主题应返回不同的 Environment 实例"""
        env1 = template_engine._get_env('classic')
        env2 = template_engine._get_env('scrapbook')
        assert env1 is not env2

    def test_cache_populated_after_call(self):
        """调用后缓存应被填充"""
        template_engine._get_env('classic')
        assert 'classic' in template_engine._env_cache

    def test_thread_safety_of_cache(self):
        """并发访问 _get_env 不应出错"""
        results = []
        errors = []

        def get_env_task(theme):
            try:
                env = template_engine._get_env(theme)
                results.append(env)
            except Exception as e:
                errors.append(e)

        threads = []
        for _ in range(10):
            t = threading.Thread(target=get_env_task, args=('classic',))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        # 所有结果应是同一实例
        assert all(r is results[0] for r in results)


# ── render ────────────────────────────────────────────────────

class TestRender:
    def setup_method(self):
        template_engine._env_cache.clear()

    def test_render_with_valid_template(self):
        """使用有效模板渲染应返回非空字符串"""
        result = template_engine.render(
            'classic', 'report.html',
            group_name='测试群',
            time_start='2024-01-01',
            time_end='2024-01-31',
            generated_at='2024-01-31 12:00',
            total_messages=100,
            total_members=20,
            avg_daily=10,
        )
        assert isinstance(result, str)
        assert len(result) > 0
        assert '测试群' in result

    def test_render_variable_substitution(self):
        """渲染应正确替换模板变量"""
        result = template_engine.render(
            'classic', 'report.html',
            group_name='我的群聊',
            time_start='2024-01-01',
            time_end='2024-01-31',
            generated_at='2024-01-31 12:00',
            total_messages=999,
            total_members=50,
            avg_daily=33,
        )
        assert '我的群聊' in result
        assert '999' in result

    def test_render_nonexistent_template_returns_empty(self):
        """渲染不存在的模板应返回空字符串"""
        result = template_engine.render('classic', 'nonexistent_template.html')
        assert result == ''

    def test_render_nonexistent_theme_falls_back(self):
        """渲染不存在的主题应回退到 classic 并正常渲染"""
        result = template_engine.render(
            'nonexistent_theme_xyz', 'report.html',
            group_name='测试',
            time_start='2024-01-01',
            time_end='2024-01-31',
            generated_at='2024-01-31',
            total_messages=0,
            total_members=0,
            avg_daily=0,
        )
        # 回退到 classic 后应能渲染
        assert isinstance(result, str)
        assert '测试' in result

    def test_render_with_temp_directory(self):
        """使用临时目录的模板渲染"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # 创建主题目录和模板
            theme_dir = os.path.join(tmp_dir, 'mytheme')
            os.makedirs(theme_dir)
            with open(os.path.join(theme_dir, 'test.html'), 'w', encoding='utf-8') as f:
                f.write('<h1>{{ title }}</h1><p>{{ content }}</p>')
            # 临时替换 _base_dir
            original_base_dir = template_engine._base_dir
            template_engine._env_cache.clear()
            try:
                template_engine._base_dir = tmp_dir
                result = template_engine.render('mytheme', 'test.html', title='你好', content='世界')
                assert '你好' in result
                assert '世界' in result
            finally:
                template_engine._base_dir = original_base_dir
                template_engine._env_cache.clear()

    def test_render_returns_string(self):
        """render 应始终返回字符串"""
        result = template_engine.render('classic', 'report.html')
        assert isinstance(result, str)


# ── list_themes ───────────────────────────────────────────────

class TestListThemes:
    def test_list_themes_returns_list(self):
        """list_themes 应返回列表"""
        themes = template_engine.list_themes()
        assert isinstance(themes, list)

    def test_list_themes_includes_classic(self):
        """list_themes 应包含 classic 主题"""
        themes = template_engine.list_themes()
        names = [t['name'] for t in themes]
        assert 'classic' in names

    def test_list_themes_includes_scrapbook(self):
        """list_themes 应包含 scrapbook 主题"""
        themes = template_engine.list_themes()
        names = [t['name'] for t in themes]
        assert 'scrapbook' in names

    def test_list_themes_has_required_fields(self):
        """每个主题应包含必要字段"""
        themes = template_engine.list_themes()
        for theme in themes:
            assert 'name' in theme
            assert 'display_name' in theme
            assert 'description' in theme
            assert 'colors' in theme
            assert 'preview_bg' in theme
            assert 'preview_accent' in theme

    def test_list_themes_with_temp_directory(self):
        """使用临时目录测试 list_themes"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # 创建主题目录
            theme_dir = os.path.join(tmp_dir, 'custom')
            os.makedirs(theme_dir)
            # 创建 theme.json
            meta = {
                'display_name': '自定义主题',
                'description': '测试主题',
                'colors': ['#ff0000'],
                'preview_bg': '#000000',
                'preview_accent': '#ff0000',
            }
            with open(os.path.join(theme_dir, 'theme.json'), 'w', encoding='utf-8') as f:
                json.dump(meta, f)
            # 临时替换 _base_dir
            original_base_dir = template_engine._base_dir
            try:
                template_engine._base_dir = tmp_dir
                themes = template_engine.list_themes()
                assert len(themes) == 1
                assert themes[0]['name'] == 'custom'
                assert themes[0]['display_name'] == '自定义主题'
                assert themes[0]['description'] == '测试主题'
                assert themes[0]['colors'] == ['#ff0000']
            finally:
                template_engine._base_dir = original_base_dir

    def test_list_themes_without_theme_json(self):
        """没有 theme.json 的目录应使用默认值"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            theme_dir = os.path.join(tmp_dir, 'basic')
            os.makedirs(theme_dir)
            # 不创建 theme.json
            original_base_dir = template_engine._base_dir
            try:
                template_engine._base_dir = tmp_dir
                themes = template_engine.list_themes()
                assert len(themes) == 1
                assert themes[0]['name'] == 'basic'
                assert themes[0]['display_name'] == 'Basic'  # name.title()
                assert themes[0]['description'] == ''
            finally:
                template_engine._base_dir = original_base_dir

    def test_list_themes_empty_directory_returns_default(self):
        """空目录应返回默认 classic 主题"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            original_base_dir = template_engine._base_dir
            try:
                template_engine._base_dir = tmp_dir
                themes = template_engine.list_themes()
                assert len(themes) == 1
                assert themes[0]['name'] == 'classic'
            finally:
                template_engine._base_dir = original_base_dir

    def test_list_themes_invalid_theme_json(self):
        """无效的 theme.json 应使用默认值"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            theme_dir = os.path.join(tmp_dir, 'broken')
            os.makedirs(theme_dir)
            with open(os.path.join(theme_dir, 'theme.json'), 'w', encoding='utf-8') as f:
                f.write('invalid json{{{')
            original_base_dir = template_engine._base_dir
            try:
                template_engine._base_dir = tmp_dir
                themes = template_engine.list_themes()
                assert len(themes) == 1
                assert themes[0]['name'] == 'broken'
                assert themes[0]['display_name'] == 'Broken'
            finally:
                template_engine._base_dir = original_base_dir

    def test_list_themes_ignores_files(self):
        """list_themes 应忽略非目录文件"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # 创建一个普通文件（非目录）
            with open(os.path.join(tmp_dir, 'readme.txt'), 'w') as f:
                f.write('not a theme')
            original_base_dir = template_engine._base_dir
            try:
                template_engine._base_dir = tmp_dir
                themes = template_engine.list_themes()
                # 没有有效主题目录，应返回默认
                assert len(themes) == 1
                assert themes[0]['name'] == 'classic'
            finally:
                template_engine._base_dir = original_base_dir


# ── _load_template (通过 _get_env 间接测试缓存) ──────────────

class TestLoadTemplate:
    def setup_method(self):
        template_engine._env_cache.clear()

    def test_cache_is_empty_initially(self):
        """初始时缓存应为空"""
        template_engine._env_cache.clear()
        assert len(template_engine._env_cache) == 0

    def test_first_call_populates_cache(self):
        """首次调用应填充缓存"""
        template_engine._env_cache.clear()
        template_engine._get_env('classic')
        assert 'classic' in template_engine._env_cache

    def test_second_call_uses_cache(self):
        """第二次调用应使用缓存"""
        env1 = template_engine._get_env('classic')
        assert 'classic' in template_engine._env_cache
        env2 = template_engine._get_env('classic')
        assert env1 is env2

    def test_cache_with_temp_directory(self):
        """使用临时目录测试缓存机制"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            theme_dir = os.path.join(tmp_dir, 'cached_theme')
            os.makedirs(theme_dir)
            with open(os.path.join(theme_dir, 'test.html'), 'w', encoding='utf-8') as f:
                f.write('{{ msg }}')
            original_base_dir = template_engine._base_dir
            template_engine._env_cache.clear()
            try:
                template_engine._base_dir = tmp_dir
                env1 = template_engine._get_env('cached_theme')
                env2 = template_engine._get_env('cached_theme')
                assert env1 is env2
                assert 'cached_theme' in template_engine._env_cache
            finally:
                template_engine._base_dir = original_base_dir
                template_engine._env_cache.clear()

    def test_invalid_theme_cached_as_classic_env(self):
        """无效主题回退到 classic 后，缓存键仍是原主题名"""
        template_engine._env_cache.clear()
        env = template_engine._get_env('totally_invalid_theme')
        # 缓存键是请求的主题名
        assert 'totally_invalid_theme' in template_engine._env_cache
        # 但 Environment 实际加载的是 classic 目录
        assert env is not None


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
