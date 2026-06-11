# 贡献指南

感谢你对本项目的关注！欢迎贡献代码、报告问题或提出改进建议。

## 🛠️ 开发环境搭建

### 前置要求

- **Python** ≥ 3.10
- **Git**
- **chatlog_alpha** — 微信数据库解密工具（可选，用于完整功能测试）

### 克隆与安装

```bash
git clone https://github.com/MeetMX-ai/chatlens.git
cd chatlens

# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 安装项目及开发依赖
pip install -e ".[dev]"

# 安装 pre-commit hooks
pre-commit install
```

### Pre-commit

项目配置了 pre-commit hooks，会在每次提交时自动运行：

- **ruff** — 代码检查和自动修复
- **ruff-format** — 代码格式化
- **trailing-whitespace / end-of-file-fixer** — 清理空白字符
- **check-yaml / check-json** — 配置文件语法检查
- **check-added-large-files** — 阻止大文件提交（>500KB）
- **check-merge-conflict** — 检查未解决的合并冲突
- **bandit** — 安全漏洞扫描

手动运行所有 hooks：

```bash
pre-commit run --all-files
```

### 配置

复制配置模板并填入你的设置：

```bash
cp config/config.json.example config/config.json
```

编辑 `config/config.json`，配置 AI 服务 API Key 等参数。

### 运行测试

```bash
pytest tests/ -v
```

### 类型检查

```bash
mypy chatlens/
```

### 代码格式化

```bash
ruff check chatlens/
ruff format chatlens/
```

---

## 📐 代码规范

### 类型注解

- 所有公开函数/方法必须有参数和返回值类型注解
- 使用 `typing` 模块中的类型（`Optional`、`List`、`Dict` 等）
- 复杂类型使用 `TypeAlias` 或 `TypeVar`

### 代码风格

- 遵循 PEP 8，使用 `ruff` 检查和格式化
- 行宽限制 120 字符
- 使用双引号表示字符串

### 命名规范

- 类名：`PascalCase`（如 `MessageProvider`）
- 函数/方法：`snake_case`（如 `get_messages`）
- 常量：`UPPER_SNAKE_CASE`（如 `EMPTY_RESULT`）
- 私有成员：前缀 `_`（如 `_bridge`）

### 测试

- 新功能必须附带单元测试
- 测试文件放在 `tests/` 目录，命名 `test_*.py`
- 使用 `pytest` 框架
- 测试应独立运行，不依赖外部服务或真实数据

### 文档

- 公开 API 使用 docstring（Google 风格）
- 复杂逻辑添加行内注释
- 配置变更更新 `config/config.json.example`

---

## 🔌 如何新增 Provider（平台适配器）

`MessageProvider` 协议定义了数据源的标准接口：

```python
from typing import Protocol, List
from chatlens.core.models import ChatMessage

class MessageProvider(Protocol):
    name: str

    def is_available(self) -> bool: ...
    def get_groups(self) -> List[str]: ...
    def get_messages(self, talker: str, limit: int = 0) -> List[ChatMessage]: ...
    def get_display_name(self, username: str) -> str: ...
    def reset_connections(self) -> None: ...
```

### 实现步骤

1. **创建 Provider 文件**：在 `chatlens/core/providers.py` 中添加新类：

```python
class QQProvider:
    name = 'qq'

    def __init__(self, bot_token: str, ...):
        # 初始化连接
        ...

    def is_available(self) -> bool:
        ...

    def get_messages(self, talker: str, limit: int = 0) -> List[ChatMessage]:
        # 拉取消息并转换为 ChatMessage 格式
        ...

    # ... 实现其他协议方法
```

2. **注册到构建流程**：在 `chatlens/main.py` 的 `_build_providers()` 中添加：

```python
qq_cfg = providers_cfg.get('qq', {})
if qq_cfg.get('enabled', False):
    providers.append(QQProvider(
        bot_token=qq_cfg.get('bot_token'),
    ))
```

3. **添加配置**：在 `config/config.json.example` 中添加新平台的配置模板

4. **编写测试**：在 `tests/` 中添加 Provider 的单元测试

---

## 🧩 如何新增插件

插件通过 `PluginRegistry` 自动发现和加载。

### 实现步骤

1. **创建插件目录**：

```
plugins/
  my_plugin/
    __init__.py
    my_service.py
```

2. **实现 Plugin 类**（`__init__.py`）：

```python
from chatlens.core import Plugin

class MyPlugin(Plugin):
    name = 'my_plugin'
    description = '我的新功能'

    def register(self, ga):
        from .my_service import MyService
        service = MyService(ga)
        ga.my_plugin = service
```

3. `PluginRegistry.discover()` 会自动发现新插件

---

## 📋 PR 流程

1. **Fork** 本仓库
2. **创建分支**：`git checkout -b feature/my-feature`
3. **开发**：编写代码，确保测试通过
4. **提交**：`git commit -m "feat: add my feature"`
5. **推送**：`git push origin feature/my-feature`
6. **创建 PR**：描述变更内容，关联相关 Issue

### Commit 规范

使用 [Conventional Commits](https://www.conventionalcommits.org/)：

- `feat:` 新功能
- `fix:` 修复 bug
- `docs:` 文档变更
- `refactor:` 代码重构
- `test:` 测试相关
- `chore:` 构建/工具变更

---

## 🐛 报告问题

请在 [Issues](https://github.com/MeetMX-ai/chatlens/issues) 中描述：

- 问题现象和复现步骤
- 环境信息（Python 版本、操作系统）
- 相关日志或截图

---

## 📄 许可证

贡献的代码将遵循项目的 [MIT License](LICENSE)。提交 PR 即表示你同意将贡献的代码以该许可证开源。
