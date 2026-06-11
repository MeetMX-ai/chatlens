"""配置文件中 chatlog webhook URL 修复验证测试

回归 Bug：chatlog webhook URL 之前硬编码为 http://localhost:8000/webhook/wechat，
而 web 服务实际监听 http://localhost:8080，导致 chatlog 服务无法回调到正确端口。

修复策略：把 webhook URL 写入 config.json / config.json.example 的 chatlog 段，
端口必须与 server.port 保持一致。
"""
import json
import os
import re
import sys
import unittest
from urllib.parse import urlparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class TestChatlogWebhookUrl(unittest.TestCase):
    """chatlog.webhook_url 配置正确性测试"""

    def _read_example(self) -> dict:
        example_path = os.path.join(ROOT, "config", "config.json.example")
        self.assertTrue(
            os.path.exists(example_path),
            f"缺少配置文件模板: {example_path}",
        )
        return _load_json(example_path)

    def test_example_has_webhook_url(self):
        """config.json.example 中必须包含 chatlog.webhook_url 字段"""
        cfg = self._read_example()
        self.assertIn("chatlog", cfg, "config.json.example 缺少 chatlog 段")
        chatlog = cfg["chatlog"]
        self.assertIn(
            "webhook_url",
            chatlog,
            "chatlog.webhook_url 缺失，集成 chatlog 时无法回调",
        )

    def test_example_webhook_url_port_matches_server(self):
        """webhook URL 的端口必须与 server.port 一致（避免回调到错误端口）"""
        cfg = self._read_example()
        server_port = cfg.get("server", {}).get("port")
        webhook_url = cfg.get("chatlog", {}).get("webhook_url")
        self.assertIsNotNone(server_port, "server.port 缺失")
        self.assertIsNotNone(webhook_url, "chatlog.webhook_url 缺失")

        parsed = urlparse(webhook_url)
        self.assertIn(
            parsed.scheme, ("http", "https"), f"webhook_url scheme 非法: {webhook_url}"
        )
        self.assertEqual(
            parsed.port,
            server_port,
            f"webhook URL 端口 ({parsed.port}) 与 server.port ({server_port}) 不一致",
        )

    def test_example_webhook_url_points_to_wechat(self):
        """webhook 路径应以 /webhook/wechat 结尾（chatlog 约定）"""
        cfg = self._read_example()
        webhook_url = cfg.get("chatlog", {}).get("webhook_url", "")
        self.assertTrue(
            webhook_url.rstrip("/").endswith("/webhook/wechat"),
            f"webhook URL 路径应以 /webhook/wechat 结尾，实际: {webhook_url}",
        )

    def test_example_webhook_url_is_localhost(self):
        """默认 webhook URL 指向 localhost（开发环境）"""
        cfg = self._read_example()
        webhook_url = cfg.get("chatlog", {}).get("webhook_url", "")
        parsed = urlparse(webhook_url)
        self.assertIn(
            parsed.hostname,
            ("localhost", "127.0.0.1"),
            f"默认 webhook URL 应指向 localhost，实际: {webhook_url}",
        )

    def test_no_legacy_port_8000_in_example(self):
        """回归：不应回退到错误的 8000 端口"""
        cfg = self._read_example()
        webhook_url = cfg.get("chatlog", {}).get("webhook_url", "")
        self.assertNotIn(
            ":8000",
            webhook_url,
            f"webhook URL 不应使用 8000 端口（应与 server.port={cfg.get('server', {}).get('port')} 一致）",
        )


class TestRuntimeConfigWebhookUrl(unittest.TestCase):
    """运行时配置文件 chatlens/config/config.json 中的 webhook URL 测试"""

    def test_runtime_config_has_webhook_url(self):
        """chatlens/config/config.json 应包含 chatlog.webhook_url"""
        runtime_path = os.path.join(
            ROOT, "chatlens", "config", "config.json"
        )
        if not os.path.exists(runtime_path):
            self.skipTest(f"运行时配置不存在: {runtime_path}")
        cfg = _load_json(runtime_path)
        self.assertIn("chatlog", cfg, "运行时配置缺少 chatlog 段")
        self.assertIn(
            "webhook_url",
            cfg["chatlog"],
            "运行时配置缺少 chatlog.webhook_url",
        )

    def test_runtime_config_webhook_port_consistent(self):
        """运行时配置 webhook URL 端口与 server.port 一致"""
        runtime_path = os.path.join(
            ROOT, "chatlens", "config", "config.json"
        )
        if not os.path.exists(runtime_path):
            self.skipTest(f"运行时配置不存在: {runtime_path}")
        cfg = _load_json(runtime_path)
        server_port = cfg.get("server", {}).get("port")
        webhook_url = cfg.get("chatlog", {}).get("webhook_url")
        if not server_port or not webhook_url:
            self.skipTest("配置缺少必要字段")
        parsed = urlparse(webhook_url)
        self.assertEqual(
            parsed.port,
            server_port,
            f"运行时配置 webhook URL 端口 ({parsed.port}) "
            f"与 server.port ({server_port}) 不一致",
        )


class TestChatlogBinaryWebhookConfig(unittest.TestCase):
    """chatlog 二进制配置文件 chatlog_alpha/chatlog-server.json 的 webhook 测试"""

    def _read_chatlog_server_json(self):
        path = os.path.join(ROOT, "chatlog_alpha", "chatlog-server.json")
        if not os.path.exists(path):
            self.skipTest(f"chatlog 配置不存在: {path}")
        return _load_json(path), path

    def test_chatlog_server_json_has_webhook_block(self):
        """chatlog-server.json 必须有 webhook 配置块（Items 数组）"""
        cfg, path = self._read_chatlog_server_json()
        self.assertIn("webhook", cfg, f"{path} 缺少 webhook 块")
        wh = cfg["webhook"]
        self.assertIsInstance(wh, dict, "webhook 必须是 dict")
        self.assertIn("items", wh, "webhook.items 缺失（chatlog binary 期望的 schema）")
        self.assertIsInstance(wh["items"], list, "webhook.items 必须是 list")
        self.assertGreater(
            len(wh["items"]), 0, "webhook.items 至少需要 1 个条目"
        )

    def test_chatlog_webhook_url_uses_server_port(self):
        """chatlog webhook URL 端口必须与 ChatLens web 服务端口一致"""
        cfg, path = self._read_chatlog_server_json()
        # 从 ChatLens 配置中读 web 服务端口
        lens_cfg_path = os.path.join(ROOT, "config", "config.json")
        if not os.path.exists(lens_cfg_path):
            lens_cfg_path = os.path.join(ROOT, "chatlens", "config", "config.json")
        if not os.path.exists(lens_cfg_path):
            self.skipTest("找不到 ChatLens 配置文件")
        lens_cfg = _load_json(lens_cfg_path)
        server_port = lens_cfg.get("server", {}).get("port", 8080)
        items = cfg.get("webhook", {}).get("items", [])
        self.assertTrue(items, "chatlog webhook.items 为空")
        for item in items:
            url = item.get("url", "")
            self.assertTrue(url, f"webhook item 缺少 url 字段: {item}")
            parsed = urlparse(url)
            self.assertEqual(
                parsed.port,
                server_port,
                f"chatlog webhook URL 端口 ({parsed.port}) "
                f"与 ChatLens server.port ({server_port}) 不一致: {url}",
            )

    def test_chatlog_webhook_url_is_8080(self):
        """回归：chatlog webhook URL 端口必须为 8080（与 web 服务一致），不能是 8000"""
        cfg, path = self._read_chatlog_server_json()
        items = cfg.get("webhook", {}).get("items", [])
        urls = [item.get("url", "") for item in items]
        for url in urls:
            self.assertNotIn(
                ":8000/",
                url,
                f"chatlog webhook URL 不应使用 8000 端口（已迁移到 8080）: {url}",
            )
            self.assertNotIn(
                ":8001/",
                url,
                f"chatlog webhook URL 不应使用 8001 端口（已迁移到 8080）: {url}",
            )
            parsed = urlparse(url)
            self.assertEqual(
                parsed.port,
                8080,
                f"chatlog webhook URL 端口应为 8080，实际: {parsed.port} ({url})",
            )

    def test_chatlog_webhook_points_to_wechat(self):
        """chatlog webhook URL 路径应以 /webhook/wechat 结尾"""
        cfg, path = self._read_chatlog_server_json()
        items = cfg.get("webhook", {}).get("items", [])
        urls = [item.get("url", "") for item in items]
        self.assertTrue(urls, "没有 webhook URL")
        for url in urls:
            self.assertTrue(
                url.rstrip("/").endswith("/webhook/wechat"),
                f"chatlog webhook URL 应以 /webhook/wechat 结尾: {url}",
            )


if __name__ == "__main__":
    unittest.main()
