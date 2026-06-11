import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from chatlens.core.models import ChatMessage
from chatlens.core.providers import MessageProvider, ProviderRegistry, WechatProvider


class FakeProvider:
    """测试用假 provider"""
    name = 'fake'

    def __init__(self, available=True):
        self._available = available

    def is_available(self):
        return self._available

    def get_groups(self):
        return ['fake_group_1', 'fake_group_2']

    def get_messages(self, talker, limit=0, start_date="", end_date=""):
        msgs = [ChatMessage(
            sender='fake_user', content='fake message', msg_type='text',
            msg_attr='', timestamp='2026-01-01 12:00:00', group_name='fake_group',
        )]
        return msgs[:limit] if limit else msgs

    def get_display_name(self, username):
        return f'Fake({username})'

    def reset_connections(self):
        pass


class TestMessageProviderProtocol:
    def test_fake_provider_satisfies_protocol(self):
        provider = FakeProvider()
        assert isinstance(provider, MessageProvider)

    def test_wechat_provider_satisfies_protocol(self):
        provider = WechatProvider(db_path=None)
        assert isinstance(provider, MessageProvider)

    def test_incomplete_provider_fails_protocol(self):
        class Incomplete:
            name = 'incomplete'
        assert not isinstance(Incomplete(), MessageProvider)


class TestProviderRegistry:
    def test_register_and_get(self):
        registry = ProviderRegistry()
        provider = FakeProvider()
        registry.register(provider)
        assert registry.get('fake') is provider

    def test_get_nonexistent(self):
        registry = ProviderRegistry()
        assert registry.get('nonexistent') is None

    def test_get_all(self):
        registry = ProviderRegistry()
        p1 = FakeProvider()
        p2 = FakeProvider()
        p2.name = 'fake2'
        registry.register(p1)
        registry.register(p2)
        assert len(registry.get_all()) == 2

    def test_get_available(self):
        registry = ProviderRegistry()
        p1 = FakeProvider(available=True)
        p2 = FakeProvider(available=False)
        p2.name = 'unavailable'
        registry.register(p1)
        registry.register(p2)
        available = registry.get_available()
        assert len(available) == 1
        assert available[0].name == 'fake'

    def test_names(self):
        registry = ProviderRegistry()
        registry.register(FakeProvider())
        assert 'fake' in registry.names()

    def test_register_overwrites_same_name(self):
        registry = ProviderRegistry()
        p1 = FakeProvider()
        p2 = FakeProvider()
        registry.register(p1)
        registry.register(p2)
        assert registry.get('fake') is p2


class TestWechatProvider:
    def test_name(self):
        provider = WechatProvider(db_path=None)
        assert provider.name == 'wechat'

    def test_bridge_property(self):
        provider = WechatProvider(db_path=None)
        assert provider.bridge is not None

    def test_is_available_returns_bool(self):
        provider = WechatProvider(db_path=None)
        assert isinstance(provider.is_available(), bool)

    def test_get_messages_returns_list(self):
        provider = WechatProvider(db_path=None)
        result = provider.get_messages('test@chatroom')
        assert isinstance(result, list)

    def test_get_groups_returns_list(self):
        provider = WechatProvider(db_path=None)
        result = provider.get_groups()
        assert isinstance(result, list)

    def test_get_display_name_returns_string(self):
        provider = WechatProvider(db_path=None)
        result = provider.get_display_name('test_user')
        assert isinstance(result, str)


class TestGroupAnalysisWithProviders:
    def test_default_creates_wechat_provider(self):
        from chatlens.core import GroupAnalysis
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({'data_dir': tmpdir})
            assert ga.providers.get('wechat') is not None

    def test_custom_providers(self):
        from chatlens.core import GroupAnalysis
        with tempfile.TemporaryDirectory() as tmpdir:
            fake = FakeProvider()
            ga = GroupAnalysis({'data_dir': tmpdir}, providers=[fake])
            assert ga.providers.get('fake') is fake
            assert ga.providers.get('wechat') is None

    def test_chatlog_backward_compat_property(self):
        from chatlens.core import GroupAnalysis
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({'data_dir': tmpdir})
            # chatlog property should return bridge or None
            cl = ga.chatlog
            assert cl is None or hasattr(cl, 'get_messages')

    def test_get_provider_method(self):
        from chatlens.core import GroupAnalysis
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({'data_dir': tmpdir})
            p = ga.get_provider('wechat')
            assert p is not None
            assert p.name == 'wechat'

    def test_load_from_provider(self):
        from chatlens.core import GroupAnalysis
        with tempfile.TemporaryDirectory() as tmpdir:
            fake = FakeProvider()
            ga = GroupAnalysis({'data_dir': tmpdir}, providers=[fake])
            messages = ga.load_from_provider('test_talker', 'fake')
            assert len(messages) > 0
            assert 'test_talker' in ga.collector_data

    def test_load_from_nonexistent_provider(self):
        from chatlens.core import GroupAnalysis
        with tempfile.TemporaryDirectory() as tmpdir:
            ga = GroupAnalysis({'data_dir': tmpdir})
            messages = ga.load_from_provider('test_talker', 'nonexistent')
            assert messages == []


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
