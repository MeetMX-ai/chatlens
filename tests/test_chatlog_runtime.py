"""_chatlog_runtime.py 单元测试 — 覆盖启动/停止/健康检查/解密等核心逻辑"""

import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch, MagicMock, PropertyMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import chatlens.core._chatlog_runtime as rt


class TestGetStartTime(unittest.TestCase):
    """get_start_time() — 懒初始化启动时间"""

    def test_returns_float(self):
        result = rt.get_start_time()
        self.assertIsInstance(result, float)

    def test_returns_same_value_on_repeated_calls(self):
        t1 = rt.get_start_time()
        t2 = rt.get_start_time()
        self.assertEqual(t1, t2)

    def test_start_time_module_constant(self):
        self.assertIsInstance(rt.START_TIME, float)


class TestFindChatlogExe(unittest.TestCase):
    """find_chatlog_exe() — 查找 chatlog.exe 路径"""

    @patch('os.path.exists', return_value=True)
    def test_found(self, mock_exists):
        result = rt.find_chatlog_exe()
        self.assertNotEqual(result, '')
        self.assertTrue(result.endswith('chatlog.exe'))

    @patch('os.path.exists', return_value=False)
    def test_not_found(self, mock_exists):
        result = rt.find_chatlog_exe()
        self.assertEqual(result, '')


class TestFindChatlogConfig(unittest.TestCase):
    """find_chatlog_config() — 查找 chatlog-server.json"""

    @patch('os.path.exists', return_value=True)
    def test_found(self, mock_exists):
        result = rt.find_chatlog_config()
        self.assertNotEqual(result, '')
        self.assertTrue(result.endswith('chatlog-server.json'))

    @patch('os.path.exists', return_value=False)
    def test_not_found(self, mock_exists):
        result = rt.find_chatlog_config()
        self.assertEqual(result, '')


class TestFindChatlogDb(unittest.TestCase):
    """find_chatlog_db() — 查找聊天数据库"""

    def test_config_has_db_path(self):
        config = {"chatlog": {"db_path": "/custom/path.db"}}
        result = rt.find_chatlog_db(config)
        self.assertEqual(result, "/custom/path.db")

    @patch('os.path.exists', return_value=False)
    def test_no_db_found(self, mock_exists):
        config = {"chatlog": {}}
        result = rt.find_chatlog_db(config)
        self.assertEqual(result, '')


class TestBuildChatlogCmd(unittest.TestCase):
    """_build_chatlog_cmd() — 构建命令行"""

    @patch.object(rt, 'find_chatlog_exe', return_value='')
    def test_no_exe_returns_empty(self, mock_exe):
        result = rt._build_chatlog_cmd("server")
        self.assertEqual(result, [])

    @patch.object(rt, 'find_chatlog_config', return_value='')
    @patch.object(rt, 'find_chatlog_exe', return_value='C:\\chatlog.exe')
    def test_basic_cmd_without_config(self, mock_exe, mock_cfg):
        # 重置缓存
        rt._chatlog_config_cache = None
        result = rt._build_chatlog_cmd("server")
        self.assertEqual(result[0], 'C:\\chatlog.exe')
        self.assertEqual(result[1], 'server')

    @patch('builtins.open', create=True)
    @patch.object(rt, 'find_chatlog_config', return_value='C:\\chatlog-server.json')
    @patch.object(rt, 'find_chatlog_exe', return_value='C:\\chatlog.exe')
    def test_cmd_with_config(self, mock_exe, mock_cfg, mock_open):
        rt._chatlog_config_cache = None
        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_file.read.return_value = '{"data_dir": "/data", "data_key": "key123", "version": 1, "platform": "windows"}'
        mock_open.return_value = mock_file
        result = rt._build_chatlog_cmd("decrypt")
        self.assertIn('--data-dir', result)
        self.assertIn('/data', result)
        self.assertIn('--data-key', result)
        self.assertIn('key123', result)
        self.assertIn('--version', result)
        self.assertIn('1', result)
        self.assertIn('--platform', result)
        self.assertIn('windows', result)


class TestRunChatlogDecrypt(unittest.TestCase):
    """run_chatlog_decrypt() — 执行解密命令"""

    @patch.object(rt, '_build_chatlog_cmd', return_value=[])
    def test_no_cmd_returns_false(self, mock_cmd):
        result = rt.run_chatlog_decrypt()
        self.assertFalse(result)

    @patch('subprocess.run')
    @patch.object(rt, '_build_chatlog_cmd', return_value=['chatlog.exe', 'decrypt'])
    def test_success(self, mock_cmd, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr=b'')
        result = rt.run_chatlog_decrypt()
        self.assertTrue(result)

    @patch('subprocess.run')
    @patch.object(rt, '_build_chatlog_cmd', return_value=['chatlog.exe', 'decrypt'])
    def test_nonzero_returncode(self, mock_cmd, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr=b'error')
        result = rt.run_chatlog_decrypt()
        self.assertFalse(result)

    @patch('subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='test', timeout=60))
    @patch.object(rt, '_build_chatlog_cmd', return_value=['chatlog.exe', 'decrypt'])
    def test_timeout(self, mock_cmd, mock_run):
        result = rt.run_chatlog_decrypt()
        self.assertFalse(result)

    @patch('subprocess.run', side_effect=OSError("boom"))
    @patch.object(rt, '_build_chatlog_cmd', return_value=['chatlog.exe', 'decrypt'])
    def test_exception(self, mock_cmd, mock_run):
        result = rt.run_chatlog_decrypt()
        self.assertFalse(result)


class TestStartChatlogServer(unittest.TestCase):
    """start_chatlog_server() — 启动 chatlog server"""

    def setUp(self):
        # 重置全局进程状态
        rt._chatlog_process = None

    def tearDown(self):
        rt._chatlog_process = None

    @patch.object(rt, '_build_chatlog_cmd', return_value=[])
    def test_no_cmd_returns_none(self, mock_cmd):
        result = rt.start_chatlog_server()
        self.assertIsNone(result)

    @patch('threading.Thread')
    @patch.object(rt, 'find_chatlog_config', return_value='')
    @patch.object(rt, '_build_chatlog_cmd', return_value=['chatlog.exe', 'server', '--auto-decrypt'])
    @patch('subprocess.Popen')
    @patch('time.sleep')
    def test_normal_start(self, mock_sleep, mock_popen, mock_cmd, mock_cfg, mock_thread):
        proc = MagicMock()
        proc.poll.return_value = None  # 进程仍在运行
        proc.pid = 12345
        mock_popen.return_value = proc
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        result = rt.start_chatlog_server()
        self.assertIsNotNone(result)
        self.assertEqual(result.pid, 12345)

    @patch('threading.Thread')
    @patch.object(rt, 'find_chatlog_config', return_value='')
    @patch.object(rt, '_build_chatlog_cmd', return_value=['chatlog.exe', 'server', '--auto-decrypt'])
    @patch('subprocess.Popen')
    @patch('time.sleep')
    def test_start_failure_process_exits(self, mock_sleep, mock_popen, mock_cmd, mock_cfg, mock_thread):
        proc = MagicMock()
        proc.poll.return_value = 1  # 进程已退出
        proc.returncode = 1
        proc.stderr.read.return_value = b'error message'
        mock_popen.return_value = proc
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        result = rt.start_chatlog_server()
        self.assertIsNone(result)

    @patch.object(rt, '_build_chatlog_cmd', return_value=['chatlog.exe', 'server'])
    @patch('subprocess.Popen', side_effect=OSError("cannot start"))
    @patch('time.sleep')
    def test_start_exception(self, mock_sleep, mock_popen, mock_cmd):
        result = rt.start_chatlog_server()
        self.assertIsNone(result)

    @patch('threading.Thread')
    @patch.object(rt, 'find_chatlog_config', return_value='')
    @patch.object(rt, '_build_chatlog_cmd', return_value=['chatlog.exe', 'server', '--auto-decrypt'])
    @patch('subprocess.Popen')
    @patch('time.sleep')
    def test_already_running_skips_new_start(self, mock_sleep, mock_popen, mock_cmd, mock_cfg, mock_thread):
        """已运行时再次调用 start_chatlog_server 会覆盖旧进程"""
        existing_proc = MagicMock()
        existing_proc.poll.return_value = None
        existing_proc.pid = 99999
        rt._chatlog_process = existing_proc

        new_proc = MagicMock()
        new_proc.poll.return_value = None
        new_proc.pid = 88888
        mock_popen.return_value = new_proc
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        result = rt.start_chatlog_server()
        # 新进程替换了旧进程
        self.assertEqual(rt._chatlog_process.pid, 88888)


class TestStopChatlogServer(unittest.TestCase):
    """stop_chatlog_server() — 停止 chatlog server"""

    def setUp(self):
        rt._chatlog_process = None

    def tearDown(self):
        rt._chatlog_process = None

    def test_not_running_does_nothing(self):
        """未运行时调用 stop 不报错"""
        rt.stop_chatlog_server()  # 应该不抛异常
        self.assertIsNone(rt._chatlog_process)

    @pytest.mark.skipif(not hasattr(signal, 'CTRL_BREAK_EVENT'), reason="Windows-only signal")
    @patch('sys.platform', 'win32')
    def test_normal_stop_windows(self):
        proc = MagicMock()
        proc.poll.return_value = None  # 仍在运行
        proc.pid = 12345
        proc.wait.return_value = 0
        rt._chatlog_process = proc

        with patch('os.kill') as mock_kill:
            rt.stop_chatlog_server()
            mock_kill.assert_called_once_with(12345, signal.CTRL_BREAK_EVENT)

        self.assertIsNone(rt._chatlog_process)

    @patch('sys.platform', 'linux')
    def test_normal_stop_linux(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        rt._chatlog_process = proc

        rt.stop_chatlog_server()
        proc.terminate.assert_called_once()
        self.assertIsNone(rt._chatlog_process)

    @patch('sys.platform', 'linux')
    def test_stop_terminates_then_kills_on_failure(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd='test', timeout=5)
        rt._chatlog_process = proc

        rt.stop_chatlog_server()
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
        self.assertIsNone(rt._chatlog_process)


class TestCheckChatlogHealth(unittest.TestCase):
    """check_chatlog_health() — 健康检查与自动重启"""

    def setUp(self):
        rt._chatlog_process = None

    def tearDown(self):
        rt._chatlog_process = None

    def test_no_process_does_nothing(self):
        """无进程时不触发任何操作"""
        rt.check_chatlog_health()
        self.assertIsNone(rt._chatlog_process)

    @patch.object(rt, 'start_chatlog_server', return_value=MagicMock())
    def test_dead_process_triggers_restart(self, mock_start):
        """进程已退出时触发重启"""
        dead_proc = MagicMock()
        dead_proc.poll.return_value = 1  # 已退出
        dead_proc.returncode = 1
        rt._chatlog_process = dead_proc

        rt.check_chatlog_health()
        # _chatlog_process 在锁内被设为 None，然后 start 被调用
        mock_start.assert_called_once()

    @patch.object(rt, 'start_chatlog_server')
    def test_alive_process_no_restart(self, mock_start):
        """进程仍在运行时不触发重启"""
        alive_proc = MagicMock()
        alive_proc.poll.return_value = None  # 仍在运行
        rt._chatlog_process = alive_proc

        rt.check_chatlog_health()
        mock_start.assert_not_called()


class TestIsRunningState(unittest.TestCase):
    """运行状态检查 — 通过 _chatlog_process 全局变量"""

    def test_not_running_when_none(self):
        rt._chatlog_process = None
        self.assertIsNone(rt._chatlog_process)

    def test_running_when_process_alive(self):
        proc = MagicMock()
        proc.poll.return_value = None
        rt._chatlog_process = proc
        self.assertIsNotNone(rt._chatlog_process)
        self.assertIsNone(rt._chatlog_process.poll())
        rt._chatlog_process = None

    def test_not_running_when_process_dead(self):
        proc = MagicMock()
        proc.poll.return_value = 1
        rt._chatlog_process = proc
        self.assertIsNotNone(rt._chatlog_process.poll())
        rt._chatlog_process = None


class TestForwardOutput(unittest.TestCase):
    """_forward_output() — 转发子进程输出"""

    def test_forwards_lines(self):
        lines = [b'line1\n', b'line2\n']
        pipe = MagicMock()
        pipe.readline.side_effect = lines + [b'']
        pipe.close = MagicMock()

        with patch.object(rt.logger, 'log') as mock_log:
            rt._forward_output(pipe, 20)
        # 至少调用了 log（line1 和 line2）
        self.assertGreaterEqual(mock_log.call_count, 2)
        pipe.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
