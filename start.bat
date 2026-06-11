@echo off
chcp 65001 >nul
title ChatLens - 微信群聊分析工具
echo ========================================
echo     ChatLens - 微信群聊分析工具
echo ========================================
echo.

set "ROOT=%~dp0"
set "VENV_PYTHON=%ROOT%.venv\Scripts\python.exe"
set "CHATLOG_DIR=%ROOT%chatlog_alpha"
set "CONFIG_FILE=%ROOT%config\config.json"

:: ========== 1. 检查 Python ==========
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python！请先安装 Python 3.10 或以上版本。
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: ========== 2. 创建虚拟环境（如不存在）==========
if not exist "%ROOT%.venv" (
    echo [信息] 未检测到虚拟环境，正在创建 .venv ...
    python -m venv "%ROOT%.venv"
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败！
        pause
        exit /b 1
    )
    echo [完成] 虚拟环境创建成功。
)

if not exist "%VENV_PYTHON%" (
    echo [错误] 虚拟环境中的 Python 不存在！
    echo 路径: %VENV_PYTHON%
    pause
    exit /b 1
)

:: ========== 3. 安装/更新依赖 ==========
echo [信息] 正在检查并安装依赖...
"%VENV_PYTHON%" -m pip install -r "%ROOT%requirements.txt" -q
if errorlevel 1 (
    echo [警告] 依赖安装可能存在问题，尝试继续启动...
) else (
    echo [完成] 依赖检查完毕。
)

:: ========== 4. 检查配置文件 ==========
if not exist "%CONFIG_FILE%" (
    echo [信息] 未找到配置文件，正在从模板创建...
    if exist "%ROOT%config\config.json.example" (
        copy "%ROOT%config\config.json.example" "%CONFIG_FILE%" >nul
        echo [完成] 配置文件已创建: config\config.json
        echo [提示] 如需配置 AI 分析，请编辑 config\config.json 填入 API Key
    ) else (
        echo [警告] 未找到配置模板，将使用默认配置启动。
    )
)

:: ========== 4.1 检查 API Key 配置 ==========
if exist "%CONFIG_FILE%" (
    findstr /C:"YOUR_API_KEY_HERE" "%CONFIG_FILE%" >nul 2>&1
    if not errorlevel 1 (
        echo.
        echo [警告] API Key 尚未配置！当前为占位符 YOUR_API_KEY_HERE
        echo [提示] AI 深度分析功能需要有效的 API Key
        echo [提示] 请编辑 config\config.json，将 api_key 替换为您的密钥
        echo [提示] 也可在 Web 界面的"设置"页面中配置
        echo.
    ) else (
        findstr /R /C:"\"api_key\".*:\"\"" "%CONFIG_FILE%" >nul 2>&1
        if not errorlevel 1 (
            echo.
            echo [警告] API Key 为空！AI 深度分析功能将不可用
            echo [提示] 请编辑 config\config.json 填入 API Key
            echo.
        )
    )
)

:: ========== 5. 检查 chatlog_alpha ==========
if not exist "%CHATLOG_DIR%" (
    echo [警告] 未找到 chatlog_alpha 目录！
    echo 部分功能（微信数据解密）将不可用。
    echo 下载地址: https://github.com/CJYKK/chatlog_backup/releases
    echo.
    choice /c YN /m "是否继续启动（不使用 chatlog）"
    if errorlevel 2 exit /b 0
)

:: ========== 6. 检查端口占用 ==========
echo [信息] 检查端口 8080 ...
netstat -ano | findstr ":8080 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [警告] 端口 8080 已被占用！
    echo 可能已有实例在运行，或被其他程序占用。
    echo.
    choice /c YN /m "是否继续启动（可能启动失败）"
    if errorlevel 2 exit /b 0
)

:: ========== 7. 启动服务 ==========
echo.
echo ========================================
echo [信息] 正在启动 ChatLens 服务...
echo [信息] 服务地址: http://localhost:8080
echo [信息] 按 Ctrl+C 可停止服务
echo ========================================
echo.

start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8080"

"%VENV_PYTHON%" -m chatlens
if errorlevel 1 (
    echo.
    echo [错误] 程序运行出错！
    pause
    exit /b 1
)

pause
