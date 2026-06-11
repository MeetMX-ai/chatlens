const API_BASE = '';
function esc(str) { const d = document.createElement('div'); d.textContent = str || ''; return d.innerHTML; }
let _loadingCount = 0;
function showGlobalLoading() {
    _loadingCount++;
    let overlay = document.getElementById('globalLoading');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'globalLoading';
        overlay.className = 'global-loading-overlay';
        overlay.innerHTML = '<div class="global-loading-spinner"></div><div class="global-loading-text">加载中...</div>';
        document.body.appendChild(overlay);
    }
    overlay.style.display = 'flex';
}
function hideGlobalLoading() {
    _loadingCount = Math.max(0, _loadingCount - 1);
    if (_loadingCount === 0) {
        const overlay = document.getElementById('globalLoading');
        if (overlay) overlay.style.display = 'none';
    }
}
const chartInstances = {};
let currentGroup = '';
let analysisData = null;
let _showAllMembers = false;
const COLORS = ['#e88d5c','#d4a04a','#5ea87a','#5a9eaf','#c47a8a','#8b7eb6','#6bb5a0','#d4a06a','#b8785c','#7aa8c4','#c4a05e','#8cb47a','#d4886a','#6a9eb0','#b8a07a','#9c7e6a','#a0b88c','#d4b87a','#7eb6a0','#c49a6a'];

async function doAutoAnalysis() {
    document.getElementById('aiLoading').style.display = 'flex';
    document.getElementById('aiEmpty').style.display = 'none';
    const loadingText = document.querySelector('#aiLoading .loading-text');
    if (loadingText) loadingText.textContent = '正在分析并生成报告，请稍候...';
    const themeEl = document.querySelector('.theme-card.active');
    const theme = themeEl?.dataset.theme || 'scrapbook';
    const range = getRangeParams();
    const mode = document.getElementById('btnAiAnalysis')?.dataset.mode || 'auto';
    const extraParams = {};
    if (mode === 'ai') {
        extraParams.use_rules = false;
    } else if (mode === 'ide') {
        // Bug 修复：调 /api/status 检查 IDE 客户端是否可用
        // 不可用时降级为 use_fallback 模式（同步返回），避免永远 pending
        const status = await getStatusCached();
        if (status && status.ide_available) {
            extraParams.use_ide = true;
        } else {
            extraParams.use_fallback = true;
            showToast('IDE 客户端未连接，自动使用本地 AI 降级', 'info');
        }
    } else if (mode === 'rules') {
        extraParams.use_rules = true;
    }
    // 两步流程：不传 generate_image，后端默认只渲染 HTML，4 阶段后停
    const res = await api('/api/report/image/submit', { method:'POST', body:JSON.stringify({group_name:currentGroup, theme, fmt:'jpg', ...range, ...extraParams}) });
    document.getElementById('aiLoading').style.display = 'none';
    if (loadingText) loadingText.textContent = '正在分析...';
    if (res && res.success && res.task_id) {
        // 异步：弹进度模态框 + SSE 订阅
        document.getElementById('ideTaskStatus').style.display = 'none';
        showReportProgress(currentGroup, theme, /*onlyHtml=*/true);
        subscribeReportProgress(res.task_id, {
            theme,
            // 截图中隐藏的 stage 列表（HTML-only 流程不显示 screenshot 行）
            onlyHtml: true,
            onSuccess: (result) => {
                hideReportProgress();
                document.getElementById('aiResults').style.display = 'block';
                if (result.html_path) {
                    // HTML 已就绪 → 弹预览，附带"生成图片"按钮
                    const htmlUrl = result.html_url || `/api/reports/download?file=${encodeURIComponent((result.html_path.split(/[\\/]/).pop())||'')}`;
                    showHtmlPreview(htmlUrl, result.html_path, result.warnings);
                    showToast('HTML 已生成，可预览并决定是否生成图片', 'success');
                } else if (result.image_url) {
                    // 兼容老路径：后端若返回 image_url（generate_image=True 模式）直接展示
                    showReportResult({ image_url: result.image_url, html_file: result.html_path });
                    showToast('报告已生成！', 'success');
                }
            },
            onError: (err) => {
                hideReportProgress();
                document.getElementById('aiEmpty').style.display = 'flex';
                showReportError(err);
            },
        });
        return;
    }
    // 提交失败：降级到旧的同步路径（不应该发生，兜底）
    document.getElementById('aiEmpty').style.display = 'flex';
    showToast('提交失败: ' + (res?.error || '未知错误'), 'error');
}

async function pollIdeTask(taskId, theme) {
    // 优化 2 (AC2)：前 5 次 500ms 快轮询，然后 1s→2s→4s→8s 指数退避
    // 1+0.5*5 + 1+2+4 + 8*15 ≈ 2.5 + 7 + 120 ≈ 129s，最坏 3 分钟
    const maxDelay = 8000;
    const maxDurationMs = 3 * 60 * 1000;
    const FAST_DELAY = 500;
    const FAST_POLL_TIMES = 5;
    const startTime = Date.now();
    let delay = FAST_DELAY;
    let fastPollCount = 0;
    for (let i = 0; ; i++) {
        if (Date.now() - startTime > maxDurationMs) break;
        await new Promise(r => setTimeout(r, delay));
        if (Date.now() - startTime > maxDurationMs) break;
        const res = await api(`/api/ide/task?task_id=${taskId}`);
        // 优化 2 (AC2)：前 5 次固定 fast 间隔，之后指数退避
        if (fastPollCount < FAST_POLL_TIMES) {
            fastPollCount++;
            delay = FAST_DELAY;
        } else {
            delay = Math.min(delay * 2, maxDelay);
        }
        if (!res || !res.success) continue;
        const task = res.task;
        if (task.status === 'completed' && task.report) {
            _renderIdeTaskSuccess(task);
            return;
        }
        if (task.status === 'failed') {
            _renderIdeTaskError(task.result?.error || '未知错误');
            return;
        }
    }
    document.getElementById('ideTaskStatus').style.display = 'none';
    document.getElementById('aiEmpty').style.display = 'flex';
    showToast('分析超时，请稍后在报告历史中查看', 'error');
}

// IDE 任务成功的 UI 渲染（供 pollIdeTask 和 subscribeIdeEvents 共用）
function _renderIdeTaskSuccess(task) {
    if (!task) return;
    document.getElementById('ideTaskStatus').style.display = 'none';
    document.getElementById('aiResults').style.display = 'block';
    if (task.report && task.report.html_url) {
        showHtmlPreview(task.report.html_url, task.report.html_file, task.report.warnings);
    }
    if (task.report && task.report.image_url) {
        showReportResult(task.report);
    }
    showToast('IDE AI 分析完成，报告已生成', 'success');
}

// IDE 任务失败的 UI 渲染（供 pollIdeTask 和 subscribeIdeEvents 共用）
function _renderIdeTaskError(errMsg) {
    document.getElementById('ideTaskStatus').style.display = 'none';
    document.getElementById('aiEmpty').style.display = 'flex';
    showToast('IDE AI 分析失败: ' + (errMsg || '未知错误'), 'error');
}

/**
 * 订阅 IDE 任务事件流（SSE）
 * 替代 setTimeout 轮询；SSE 断线时降级到 pollIdeTask
 * @param {string} taskId - 要监听的任务 ID
 * @param {object} opts - { onSuccess, onError, theme } 回调与上下文，复用 UI 渲染
 * @returns {EventSource|null}
 */
function subscribeIdeEvents(taskId, opts = {}) {
    // 浏览器不支持 SSE 或参数缺失，直接降级到轮询
    if (typeof EventSource === 'undefined' || !taskId) {
        console.warn('[chatlens] EventSource 不可用或缺少 taskId，降级到轮询');
        return pollIdeTask(taskId, opts.theme);
    }

    // 任务级去重：同一 taskId 多次订阅只会处理一次结果
    if (subscribeIdeEvents._activeTasks.has(taskId)) {
        console.log('[chatlens] taskId 已订阅，跳过重复 SSE', taskId);
        return null;
    }
    subscribeIdeEvents._activeTasks.add(taskId);

    const es = new EventSource('/api/ide/events');
    let resolved = false;
    let retryCount = 0;
    const MAX_RETRY = 3;

    const finish = (fn) => {
        if (resolved) return;
        resolved = true;
        subscribeIdeEvents._activeTasks.delete(taskId);
        try { es.close(); } catch (e) { /* ignore */ }
        if (typeof fn === 'function') fn();
    };

    es.onopen = () => {
        console.log('[chatlens] SSE 已连接, taskId=', taskId);
        retryCount = 0;
    };

    es.onmessage = (e) => {
        let event;
        try { event = JSON.parse(e.data); } catch (err) {
            console.warn('[chatlens] SSE 消息解析失败', err);
            return;
        }
        if (!event || event.task_id !== taskId) return; // 不是我的任务，忽略

        console.log('[chatlens] 收到 SSE 事件:', event);

        if (event.type === 'ide_result_ready') {
            // 拉取完整结果，触发 UI 更新
            api(`/api/ide/task?task_id=${taskId}`).then(res => {
                if (res && res.success && res.task && res.task.status === 'completed') {
                    finish(() => {
                        _renderIdeTaskSuccess(res.task);
                        if (typeof opts.onSuccess === 'function') opts.onSuccess(res.task);
                    });
                } else {
                    finish(() => {
                        const msg = '任务未完成: ' + JSON.stringify(res);
                        _renderIdeTaskError(msg);
                        if (typeof opts.onError === 'function') opts.onError(msg);
                    });
                }
            }).catch(err => {
                finish(() => {
                    _renderIdeTaskError(err.message);
                    if (typeof opts.onError === 'function') opts.onError(err.message);
                });
            });
        } else if (event.type === 'task_failed') {
            finish(() => {
                _renderIdeTaskError(event.error || '任务失败');
                if (typeof opts.onError === 'function') opts.onError(event.error || '任务失败');
            });
        } else if (event.type === 'task_completed') {
            // 报告生成完成（可能不通过 IDE 路径）
            api(`/api/ide/task?task_id=${taskId}`).then(res => {
                if (res && res.success) {
                    finish(() => {
                        if (res.task && res.task.status === 'completed') {
                            _renderIdeTaskSuccess(res.task);
                        }
                        if (typeof opts.onSuccess === 'function') opts.onSuccess(res.task);
                    });
                }
            });
        }
        // 其他事件类型（task_created 等）忽略
    };

    es.onerror = () => {
        console.warn('[chatlens] SSE 连接出错，retryCount=', retryCount);
        retryCount++;
        if (retryCount > MAX_RETRY) {
            console.warn('[chatlens] SSE 重试超过上限，降级到轮询');
            try { es.close(); } catch (e) { /* ignore */ }
            subscribeIdeEvents._activeTasks.delete(taskId);
            if (!resolved) {
                resolved = true;
                pollIdeTask(taskId, opts.theme);
            }
        }
        // EventSource 会自动重连，<=MAX_RETRY 时不主动干预
    };

    return es;
}
// 任务级订阅去重表（避免同一 taskId 重复建立 SSE 连接）
subscribeIdeEvents._activeTasks = new Set();

// ════════════════════════════════════════════════════════════════════
//  报告异步生成：进度模态框 + SSE 订阅 + 错误高亮
// ════════════════════════════════════════════════════════════════════

// 默认 stage 顺序（含 screenshot 阶段，老流程用）
const REPORT_STAGE_ORDER_FULL = ['loading', 'stats', 'ai', 'render', 'render_done', 'screenshot'];
// HTML-only 流程的 stage 顺序（不含 screenshot），新两步流程用
const REPORT_STAGE_ORDER_HTML = ['loading', 'stats', 'ai', 'render', 'render_done'];
// 截图任务用
const REPORT_STAGE_ORDER_SCREENSHOT = ['screenshot'];
let REPORT_STAGE_ORDER = REPORT_STAGE_ORDER_FULL;
const REPORT_STAGE_LABEL = {
    loading: '加载消息',
    stats: '统计分析',
    ai: 'AI 分析',
    render: '渲染 HTML',
    render_done: 'HTML 已就绪',
    screenshot: '截图',
};
const REPORT_STAGE_HINT = {
    CHATLOG_NO_MESSAGES: '请确认 chatlog 服务在线，且该群有聊天记录',
    REPORT_RENDER_ERROR: '检查 Jinja2 模板是否完整（chatlens/plugins/report/report_templates/<theme>/）',
    REPORT_SCREENSHOT_ERROR: '请确认已安装 Chrome 或 html2image；查看 logs_web.err',
    REPORT_SCREENSHOT_EMPTY: 'Chrome 进程已退出但未生成图片，请检查 logs_web.err',
    REPORT_HTML_MISSING: '请确认 HTML 报告文件未过期被清理（>24h 自动清理）',
    INTERNAL_ERROR: '请查看 logs_web.err 中的 stacktrace',
};

function showReportProgress(groupName, theme, onlyHtml = false) {
    const modal = document.getElementById('reportProgressModal');
    if (!modal) return;
    // 根据 onlyHtml 选择 stage 列表（隐藏 screenshot 行）
    REPORT_STAGE_ORDER = onlyHtml ? REPORT_STAGE_ORDER_HTML : REPORT_STAGE_ORDER_FULL;
    document.getElementById('rpmGroupName').textContent = `${groupName} · ${theme || 'scrapbook'}`;
    document.getElementById('rpmMsg').textContent = '任务已提交...';
    document.getElementById('rpmMsg').classList.remove('error');
    document.getElementById('rpmBar').classList.remove('failed');
    document.getElementById('rpmBar').style.width = '0%';
    document.getElementById('rpmBarText').textContent = '0%';
    // 只显示当前 stage 列表里的 li：隐藏未列入的
    const allLi = document.getElementById('rpmStages').querySelectorAll('li');
    allLi.forEach(li => {
        li.classList.remove('active', 'done', 'failed');
        const st = li.dataset.stage;
        li.style.display = REPORT_STAGE_ORDER.includes(st) ? '' : 'none';
    });
    document.getElementById('rpmCancelBtn').style.display = '';
    document.getElementById('rpmMinBtn').textContent = '最小化';
    modal.style.display = 'block';
    modal.classList.remove('minimized');
}

function showScreenshotProgress() {
    // 第二步：截图任务专用的简化进度模态框
    const modal = document.getElementById('reportProgressModal');
    if (!modal) return;
    REPORT_STAGE_ORDER = REPORT_STAGE_ORDER_SCREENSHOT;
    document.getElementById('rpmGroupName').textContent = '🖼️ 生成图片';
    document.getElementById('rpmMsg').textContent = '准备截图...';
    document.getElementById('rpmMsg').classList.remove('error');
    document.getElementById('rpmBar').classList.remove('failed');
    document.getElementById('rpmBar').style.width = '0%';
    document.getElementById('rpmBarText').textContent = '0%';
    const allLi = document.getElementById('rpmStages').querySelectorAll('li');
    allLi.forEach(li => {
        li.classList.remove('active', 'done', 'failed');
        const st = li.dataset.stage;
        li.style.display = REPORT_STAGE_ORDER.includes(st) ? '' : 'none';
    });
    document.getElementById('rpmCancelBtn').style.display = '';
    document.getElementById('rpmMinBtn').textContent = '最小化';
    modal.style.display = 'block';
    modal.classList.remove('minimized');
}

function hideReportProgress() {
    const modal = document.getElementById('reportProgressModal');
    if (modal) modal.style.display = 'none';
}

function _setReportProgress(stage, progress, message) {
    document.getElementById('rpmBar').style.width = `${Math.max(0, Math.min(100, progress || 0))}%`;
    document.getElementById('rpmBarText').textContent = `${progress || 0}%`;
    document.getElementById('rpmMsg').textContent = message || '';
    const stageIdx = REPORT_STAGE_ORDER.indexOf(stage);
    document.getElementById('rpmStages').querySelectorAll('li').forEach(li => {
        const st = li.dataset.stage;
        li.classList.remove('active', 'done', 'failed');
        // 当前 stage 直接高亮；之前的 stage 标 done（仅限同流程的 stage）
        if (st === stage) {
            li.classList.add('active');
        } else if (stageIdx > 0) {
            const stIdx = REPORT_STAGE_ORDER.indexOf(st);
            if (stIdx >= 0 && stIdx < stageIdx) {
                li.classList.add('done');
            }
        }
    });
}

function _setReportError(stage, errorObj) {
    document.getElementById('rpmBar').classList.add('failed');
    document.getElementById('rpmBar').style.width = '100%';
    document.getElementById('rpmBarText').textContent = 'FAILED';
    const stageIdx = REPORT_STAGE_ORDER.indexOf(stage);
    document.getElementById('rpmStages').querySelectorAll('li').forEach(li => {
        li.classList.remove('active', 'done', 'failed');
        const st = li.dataset.stage;
        if (st === stage) {
            li.classList.add('failed');
        } else if (stageIdx > 0) {
            const stIdx = REPORT_STAGE_ORDER.indexOf(st);
            if (stIdx >= 0 && stIdx < stageIdx) {
                li.classList.add('done');
            }
        }
    });
    const code = errorObj?.code || 'INTERNAL_ERROR';
    const msg = errorObj?.message || '未知错误';
    const hint = errorObj?.hint || REPORT_STAGE_HINT[code] || '请稍后重试或查看 logs_web.err';
    const msgEl = document.getElementById('rpmMsg');
    msgEl.classList.add('error');
    msgEl.innerHTML = `❌ <b>${esc(code)}</b>：${esc(msg)}<span class="hint">💡 ${esc(hint)}</span>`;
    document.getElementById('rpmCancelBtn').textContent = '关闭';
    document.getElementById('rpmMinBtn').textContent = '关闭';
}

function showReportError(err) {
    // 错误兜底（如果 SSE 通道没收到 report_progress 事件但 task 已 failed）
    const code = err?.code || 'INTERNAL_ERROR';
    const msg = err?.message || err?.toString() || '未知错误';
    const stage = err?.stage || 'loading';
    _setReportError(stage, { code, message: msg, hint: err?.hint });
    const m = document.getElementById('reportProgressModal');
    if (m) m.style.display = 'block';
    // 3 秒后关闭按钮改回"关闭"
    setTimeout(() => {
        const c = document.getElementById('rpmCancelBtn');
        const mm = document.getElementById('rpmMinBtn');
        if (c) c.textContent = '关闭';
        if (mm) mm.textContent = '关闭';
    }, 3000);
}

let _reportEs = null;
let _reportTaskId = null;

function subscribeReportProgress(taskId, opts = {}) {
    if (!taskId) return;
    if (typeof EventSource === 'undefined') {
        console.warn('[chatlens] EventSource 不可用，降级到轮询');
        return _pollReportStatus(taskId, opts);
    }
    if (_reportEs) {
        try { _reportEs.close(); } catch (e) {}
    }
    _reportTaskId = taskId;
    const es = new EventSource('/api/ide/events');
    _reportEs = es;
    let resolved = false;

    const finish = (fn) => {
        if (resolved) return;
        resolved = true;
        try { es.close(); } catch (e) {}
        _reportEs = null;
        if (typeof fn === 'function') fn();
    };

    es.onmessage = (e) => {
        let ev;
        try { ev = JSON.parse(e.data); } catch (err) { return; }
        if (!ev || ev.task_id !== taskId) return;
        if (ev.type === 'report_progress') {
            // 终态：done（截图完成）/ render_done（HTML 已就绪，等用户决定）/ failed（失败）
            if ((ev.stage === 'done' || ev.stage === 'render_done') && ev.result) {
                finish(() => opts.onSuccess?.(ev.result));
            } else if (ev.stage === 'failed' || ev.error) {
                finish(() => opts.onError?.(ev.error || { code: 'INTERNAL_ERROR', message: ev.message }));
            } else {
                _setReportProgress(ev.stage, ev.progress, ev.message);
            }
        }
    };
    es.onerror = () => {
        console.warn('[chatlens] 报告 SSE 出错，降级到轮询');
        finish(() => _pollReportStatus(taskId, opts));
    };
    // 兜底：30s 没收到任何事件，也启动轮询
    setTimeout(() => {
        if (!resolved) {
            console.log('[chatlens] 报告 SSE 无事件 30s，并行启动轮询');
            _pollReportStatus(taskId, opts);
        }
    }, 30000);
}

async function _pollReportStatus(taskId, opts) {
    let lastProgress = -1;
    for (let i = 0; i < 240; i++) {  // 8 分钟
        await new Promise(r => setTimeout(r, 2000));
        if (_reportTaskId !== taskId) return;  // 用户已切任务
        const res = await api(`/api/report/image/status/${taskId}`);
        if (!res || !res.success) continue;
        if ((res.stage === 'done' || res.stage === 'render_done') && res.result) {
            opts.onSuccess?.(res.result);
            return;
        }
        if (res.stage === 'failed' && res.error) {
            opts.onError?.(res.error);
            return;
        }
        if (res.progress !== lastProgress) {
            _setReportProgress(res.stage, res.progress, res.message);
            lastProgress = res.progress;
        }
    }
    opts.onError?.({ code: 'POLL_TIMEOUT', message: '轮询 8 分钟仍未完成，请检查 logs_web.err' });
}

// 按钮绑定（DOMContentLoaded 时注册）
document.addEventListener('DOMContentLoaded', () => {
    const minBtn = document.getElementById('rpmMinBtn');
    if (minBtn) minBtn.onclick = () => {
        const m = document.getElementById('reportProgressModal');
        if (m) m.classList.toggle('minimized');
        minBtn.textContent = m?.classList.contains('minimized') ? '展开' : '最小化';
    };
    const cancelBtn = document.getElementById('rpmCancelBtn');
    if (cancelBtn) cancelBtn.onclick = () => {
        hideReportProgress();
        if (_reportEs) { try { _reportEs.close(); } catch (e) {} _reportEs = null; }
    };
});

function getRangeParams() {
    const start = document.getElementById('rangeStartDate')?.value || '';
    const end = document.getElementById('rangeEndDate')?.value || '';
    const params = {};
    if (start) params.start_date = start;
    if (end) params.end_date = end;
    return params;
}

function showThemePreview(theme) {
    const modal = document.getElementById('themePreviewModal');
    const nameEl = document.getElementById('themePreviewName');
    const bodyEl = document.getElementById('themePreviewBody');
    if (!modal || !nameEl || !bodyEl) return;

    const themeData = {
        scrapbook: {
            name: '📒 手账风',
            html: '<div style="background:#faf5ee;border-radius:8px;padding:20px;font-family:serif;border:2px dashed #d4a853;"><div style="color:#8b5e3c;font-size:18px;font-weight:bold;margin-bottom:8px;">📋 群聊周报</div><div style="color:#d4a853;font-size:12px;margin-bottom:12px;">✨ 2024年1月 第3周 ✨</div><div style="background:#fff;border-radius:6px;padding:12px;margin-bottom:8px;border-left:3px solid #e07850;"><div style="color:#8b5e3c;font-weight:600;">🏆 活跃之星</div><div style="color:#a09484;font-size:13px;">小明 · 发言 128 次</div></div><div style="background:#fff;border-radius:6px;padding:12px;border-left:3px solid #d4a853;"><div style="color:#8b5e3c;font-weight:600;">💬 金句</div><div style="color:#a09484;font-size:13px;font-style:italic;">"今天天气真好！"</div></div></div>'
        },
        classic: {
            name: '🎨 经典',
            html: '<div style="background:#ffffff;border-radius:8px;padding:20px;font-family:sans-serif;border:1px solid #e0e0e0;"><div style="color:#6366f1;font-size:18px;font-weight:bold;margin-bottom:8px;border-bottom:2px solid #6366f1;padding-bottom:8px;">群聊分析报告</div><div style="display:flex;gap:12px;margin-bottom:12px;"><div style="flex:1;background:#f8f9fa;border-radius:6px;padding:10px;text-align:center;"><div style="font-size:20px;font-weight:700;color:#6366f1;">256</div><div style="font-size:11px;color:#999;">消息总数</div></div><div style="flex:1;background:#f8f9fa;border-radius:6px;padding:10px;text-align:center;"><div style="font-size:20px;font-weight:700;color:#10b981;">12</div><div style="font-size:11px;color:#999;">活跃成员</div></div></div><div style="background:#f8f9fa;border-radius:6px;padding:10px;"><div style="color:#333;font-weight:600;font-size:13px;">📊 日均消息</div><div style="color:#6366f1;font-size:16px;font-weight:700;">36.5</div></div></div>'
        },
        hack: {
            name: '⚡ 赛博朋克',
            html: '<div style="background:#0d1117;border-radius:8px;padding:20px;font-family:monospace;border:1px solid #00ff41;"><div style="color:#00ff41;font-size:14px;margin-bottom:8px;">$ chatlens --analyze --mode=cyber</div><div style="color:#f778ba;font-size:18px;font-weight:bold;margin-bottom:12px;">█▀▀ 群聊分析报告 ▀▀█</div><div style="background:rgba(0,255,65,0.08);border-radius:4px;padding:10px;margin-bottom:8px;border-left:2px solid #00ff41;"><span style="color:#00ff41;">[STATS]</span> <span style="color:#c9d1d9;">消息总数: </span><span style="color:#f778ba;">256</span></div><div style="background:rgba(0,255,65,0.08);border-radius:4px;padding:10px;border-left:2px solid #f778ba;"><span style="color:#f778ba;">[TOP]</span> <span style="color:#c9d1d9;">活跃用户: </span><span style="color:#00ff41;">小明</span></div></div>'
        }
    };

    const data = themeData[theme];
    if (!data) return;

    nameEl.textContent = data.name;
    bodyEl.innerHTML = data.html;
    modal.style.display = 'flex';
}

function showReportResult(report) {
    let resultBar = document.getElementById('reportResultBar');
    if (!resultBar) {
        resultBar = document.createElement('div');
        resultBar.id = 'reportResultBar';
        resultBar.className = 'report-result-bar';
        const aiSection = document.querySelector('#page-ai .ai-header');
        if (aiSection) aiSection.after(resultBar);
    }
    let html = '<div class="report-result-info">📄 报告已生成：</div><div class="report-result-actions">';
    if (report.image_url) {
        html += `<a href="${report.image_url}" target="_blank" class="btn btn-primary btn-sm">🖼️ 查看图片</a>`;
    }
    if (report.html_url) {
        html += `<a href="${report.html_url}" target="_blank" class="btn btn-outline btn-sm">🌐 查看 HTML</a>`;
    }
    html += '</div>';
    resultBar.innerHTML = html;
    resultBar.style.display = 'flex';
}

function showToast(msg, type='info') {
    const c = document.getElementById('toastContainer');
    const t = document.createElement('div');
    t.className = `toast ${type}`;
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(() => t.remove(), 3000);
}

async function api(path, opts={}) {
    const isMutation = opts.method && opts.method !== 'GET';
    const isAnalysis = path.includes('/analysis/');
    if (isMutation) showGlobalLoading();
    try {
        const controller = new AbortController();
        const timeout = isAnalysis ? 120000 : 30000;
        const timeoutId = setTimeout(() => controller.abort(), timeout);
        const res = await fetch(API_BASE + path, { headers:{'Content-Type':'application/json'}, signal: controller.signal, ...opts });
        clearTimeout(timeoutId);
        const data = await res.json();
        if (!res.ok || (data.success === false && data.error)) {
            const errMsg = data.error || `请求失败 (${res.status})`;
            showToast(errMsg, 'error');
            return data;
        }
        return data;
    } catch(e) {
        console.error('API error:', e);
        let msg = '网络请求失败';
        if (e.name === 'AbortError') {
            msg = isAnalysis ? '分析超时（2分钟），后台可能仍在运行，请稍后查看报告历史' : '请求超时，请检查服务器是否正常运行';
        } else if (e.message && e.message.includes('Failed to fetch')) {
            msg = '无法连接服务器，请确认服务已启动（http://localhost:8080）';
        } else if (e.message) {
            msg = '请求失败: ' + e.message;
        }
        showToast(msg, 'error');
        return null;
    } finally {
        if (isMutation) hideGlobalLoading();
    }
}

function destroyChart(id) {
    if (chartInstances[id]) { chartInstances[id].destroy(); delete chartInstances[id]; }
}
function updateOrCreateChart(id, makeConfig) {
    const cfg = makeConfig();
    const c = chartInstances[id];
    if (c && c.config.type === cfg.type) {
        c.data = cfg.data; c.options = cfg.options; c.update('none');
        return;
    }
    if (c) c.destroy();
    chartInstances[id] = new Chart(document.getElementById(id), cfg);
}

// F8: 关闭 Chart.js 默认 1s 缓动 — 切群时 6 个图同时 1s 动画拖慢感知
const barLineDefaults = {
    animation: { duration: 0 },
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { labels: { color:'#6b5e52', font:{family:"'Noto Sans SC',sans-serif",size:11} } } },
    scales: {
        x: { ticks:{color:'#a09484',font:{size:11}}, grid:{color:'rgba(45,37,32,0.08)'} },
        y: { ticks:{color:'#a09484',font:{size:11}}, grid:{color:'rgba(45,37,32,0.08)'} },
    },
};
const pieRadarDefaults = {
    animation: { duration: 0 },
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { labels: { color:'#6b5e52', font:{family:"'Noto Sans SC',sans-serif",size:11} } } },
};
function makeChart(id, type, data, optionsOverride = {}) {
    const base = (type === 'bar' || type === 'line') ? barLineDefaults : pieRadarDefaults;
    return updateOrCreateChart(id, () => ({ type, data, options: { ...base, ...optionsOverride } }));
}

// F1 修复: 把 6 个 chart 拆成首屏 2 个 + 懒加载 4 个, 错开主线程长任务
// - scheduleIdle: requestIdleCallback 包装, 首屏 2 个 chart 之间错开 (timeout 200ms)
// - observeChartCreate: IntersectionObserver 包装, 懒加载 4 个 chart 滚到才画 (rootMargin 100px)
// - destroyChart 切群重建流程保持不变, 这里只改"创建时机" (F5 才修"复用 vs 重建")
// 注: Chart.js 走 CDN UMD 没改本地化/tree-shake, 避免引入新依赖/改构建, 体积优化留给后续 PR
const _lazyChartObservers = new Map();
const scheduleIdle = window.requestIdleCallback
    ? (cb) => requestIdleCallback(cb, { timeout: 200 })
    : (cb) => setTimeout(cb, 0);
function observeChartCreate(canvasId, createFn) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    if (_lazyChartObservers.has(canvasId)) {
        _lazyChartObservers.get(canvasId).disconnect();
        _lazyChartObservers.delete(canvasId);
    }
    destroyChart(canvasId);
    if (typeof IntersectionObserver === 'undefined') {
        createFn();
        return;
    }
    const observer = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting) {
            observer.disconnect();
            _lazyChartObservers.delete(canvasId);
            createFn();
        }
    }, { rootMargin: '100px' });
    observer.observe(canvas.parentElement);
    _lazyChartObservers.set(canvasId, observer);
}

function updateWelcomeGuide() {
    const guide = document.getElementById('welcomeGuide');
    const statsGrid = document.getElementById('statsGrid');
    if (!guide || !statsGrid) return;
    if (currentGroup && analysisData) {
        guide.style.display = 'none';
        statsGrid.style.display = '';
    } else {
        guide.style.display = '';
        statsGrid.style.display = 'none';
    }
}

function renderDashboard(data) {
    if (!data) return;
    const ov = data.overview || {};
    document.getElementById('statTotalMsg').textContent = ov.total_messages || 0;
    document.getElementById('statTotalMembers').textContent = ov.total_members || 0;
    document.getElementById('statAvgPerDay').textContent = ov.avg_messages_per_day || 0;
    const tr = ov.time_range || {};
    document.getElementById('statTimeRange').textContent = tr.start && tr.end ? `${tr.start.slice(5,10)} ~ ${tr.end.slice(5,10)}` : '-';

    // F1: 首屏 2 个 chart 立即创建, requestIdleCallback 错开主线程长任务
    const hourly = data.hourly_distribution || [];
    const types = data.msg_type_distribution || [];
    scheduleIdle(() => makeChart('hourlyChart', 'bar',
        { labels:hourly.map(h=>h.label), datasets:[{label:'消息数',data:hourly.map(h=>h.count),backgroundColor:hourly.map(h=>h.count>0?'rgba(232,141,92,0.7)':'rgba(232,141,92,0.15)'),borderRadius:4,borderSkipped:false}] },
        { plugins:{legend:{display:false}} }
    ));
    scheduleIdle(() => makeChart('typeChart', 'doughnut',
        { labels:types.map(t=>t.label), datasets:[{data:types.map(t=>t.count),backgroundColor:COLORS.slice(0,types.length),borderWidth:0}] },
        { plugins:{legend:{position:'right',labels:{color:'#6b5e52',font:{size:11},padding:12}}} }
    ));

    // F1: 懒加载 3 个 chart, IntersectionObserver 滚到才画 (rootMargin 100px)
    const daily = data.daily_trend || [];
    observeChartCreate('dailyChart', () => makeChart('dailyChart', 'line',
        { labels:daily.map(d=>d.date.slice(5)), datasets:[{label:'消息数',data:daily.map(d=>d.count),borderColor:'#e88d5c',backgroundColor:'rgba(232,141,92,0.1)',fill:true,tension:0.4,pointRadius:3,pointBackgroundColor:'#e88d5c'}] },
        { plugins:{legend:{display:false}} }
    ));

    const weekdays = data.weekday_distribution || [];
    observeChartCreate('weekdayChart', () => makeChart('weekdayChart', 'bar',
        { labels:weekdays.map(w=>w.label), datasets:[{label:'消息数',data:weekdays.map(w=>w.count),backgroundColor:weekdays.map((_,i)=>i<5?'rgba(94,168,122,0.6)':'rgba(212,160,74,0.6)'),borderRadius:4,borderSkipped:false}] },
        { plugins:{legend:{display:false}} }
    ));

    observeChartCreate('trendHourlyChart', () => makeChart('trendHourlyChart', 'radar',
        { labels:hourly.map(h=>h.label), datasets:[{label:'消息数',data:hourly.map(h=>h.count),borderColor:'#e88d5c',backgroundColor:'rgba(232,141,92,0.15)',pointBackgroundColor:'#e88d5c'}] },
        { scales:{r:{ticks:{color:'#a09484',backdropColor:'transparent'},grid:{color:'rgba(45,37,32,0.08)'},pointLabels:{color:'#6b5e52',font:{size:9}}}}, plugins:{legend:{display:false}} }
    ));

    const interList = document.getElementById('interactionList');
    const interactions = (data.interaction_analysis || {}).top_interactions || [];
    if (!interactions.length) { interList.innerHTML = '<p class="empty-hint">暂无互动数据</p>'; }
    else {
        interList.innerHTML = '';
        interactions.forEach(item => {
            const div = document.createElement('div');
            div.className = 'interaction-item';
            div.innerHTML = `<div class="interaction-pair"><span>${esc(item.pair[0])}</span><span class="arrow">↔</span><span>${esc(item.pair[1])}</span></div><span class="interaction-count">${item.count} 次</span>`;
            interList.appendChild(div);
        });
    }

    renderMembers(data);
    updateWelcomeGuide();
}

function renderMembers(data) {
    if (!data) return;
    const all = data.member_stats || [];
    const members = all.slice(0,15);
    // F1: 懒加载 1 个 chart, IntersectionObserver 滚到才画 (rootMargin 100px)
    observeChartCreate('memberChart', () => makeChart('memberChart', 'bar',
        {
            labels:members.map(m=>m.sender),
            datasets:[
                {label:'文本',data:members.map(m=>m.text_count),backgroundColor:'rgba(232,141,92,0.7)',borderRadius:2},
                {label:'图片',data:members.map(m=>m.image_count),backgroundColor:'rgba(94,168,122,0.7)',borderRadius:2},
                {label:'其他',data:members.map(m=>m.other_count),backgroundColor:'rgba(212,160,74,0.7)',borderRadius:2},
            ]
        },
        { indexAxis:'y', scales:{x:{stacked:true,ticks:{color:'#a09484'},grid:{color:'rgba(45,37,32,0.08)'}},y:{stacked:true,ticks:{color:'#6b5e52',font:{size:12}},grid:{display:false}}} }
    ));
    const tbody = document.querySelector('#memberTable tbody');
    const rows = _showAllMembers ? all : all.slice(0, 50);
    const frag = document.createDocumentFragment();
    rows.forEach((m,i) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${i+1}</td><td style="font-weight:600">${esc(m.sender)}</td><td style="font-family:var(--font-mono)">${m.msg_count}</td><td>${m.text_count}</td><td>${m.image_count}</td><td>${m.other_count}</td><td style="color:var(--accent)">${m.msg_percentage}%</td><td>${m.avg_chars_per_msg}</td>`;
        frag.appendChild(tr);
    });
    if (all.length > 50) {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td colspan="8" style="text-align:center;padding:8px"><button class="btn btn-outline btn-sm" id="btnToggleMembers">${_showAllMembers ? '显示前 50 名' : `显示全部 ${all.length} 名`}</button></td>`;
        frag.appendChild(tr);
    }
    tbody.replaceChildren(frag);
    const btn = document.getElementById('btnToggleMembers');
    if (btn) btn.addEventListener('click', () => {
        _showAllMembers = !_showAllMembers;
        renderMembers(analysisData);
    });
}

async function loadStats() {
    if (!currentGroup) { showToast('请先选择群聊','error'); return; }
    showGlobalLoading();
    try {
        const res = await api(`/api/analysis/stats?group=${encodeURIComponent(currentGroup)}`);
        if (res && res.success) {
            analysisData = res.data;
            renderDashboard(analysisData);
            // H1/H4 修复：loadStats 完成不再自动调 loadDashboardInsight（重活），
            // 改为 dashboard insight 区域进入视口时再请求，或用户点了 AI 分析按钮才请求。
            // 这里只保证 stats 数据渲染完成，AI insight 由 loadDashboardInsightOnView() / 按钮触发。
        } else {
            showToast('加载统计数据失败: ' + (res?.error || '未知错误'), 'error');
        }
    } catch(e) {
        showToast('加载失败: ' + e.message, 'error');
    } finally {
        hideGlobalLoading();
    }
}

const _insightCache = new Map();
const _INSIGHT_TTL = 5 * 60 * 1000;
const _INSIGHT_MAX = 8;
function _insightCacheGet(key) {
    const entry = _insightCache.get(key);
    if (!entry) return null;
    if (Date.now() - entry._ts > _INSIGHT_TTL) { _insightCache.delete(key); return null; }
    _insightCache.delete(key); _insightCache.set(key, entry);
    return entry.data;
}
function _insightCacheSet(key, data) {
    if (_insightCache.has(key)) _insightCache.delete(key);
    _insightCache.set(key, { _ts: Date.now(), data });
    if (_insightCache.size > _INSIGHT_MAX) _insightCache.delete(_insightCache.keys().next().value);
}
function renderDashboardInsight(data) {
    const insightEl = document.getElementById('dashboardInsight');
    if (!data) { insightEl.style.display = 'none'; return; }
    document.getElementById('dashInsightSummary').textContent = (data.summary || {}).summary || '';
    const titlesDiv = document.getElementById('dashInsightTitles');
    titlesDiv.innerHTML = '';
    const titles = (data.user_titles || {}).user_titles || [];
    titles.slice(0, 5).forEach(ut => {
        const div = document.createElement('div');
        div.className = 'user-title-item';
        div.innerHTML = `<div class="ut-main"><span class="ut-name">${esc(ut.name)}</span><span class="ut-title">${esc(ut.title)}</span></div><div class="ut-meta"><span class="ut-reason">${esc(ut.reason)}</span></div>`;
        titlesDiv.appendChild(div);
    });
    const quotesDiv = document.getElementById('dashInsightQuotes');
    quotesDiv.innerHTML = '';
    const quotes = (data.golden_quotes || {}).golden_quotes || [];
    quotes.slice(0, 3).forEach(gq => {
        const div = document.createElement('div');
        div.className = 'golden-quote-item';
        div.innerHTML = `<div class="gq-content">"${esc(gq.content)}"</div><div class="gq-meta"><span class="gq-sender">— ${esc(gq.sender)}</span></div>`;
        quotesDiv.appendChild(div);
    });
    const qualityDiv = document.getElementById('dashInsightQuality');
    qualityDiv.innerHTML = '';
    const quality = data.chat_quality || {};
    if (quality.title) {
        qualityDiv.innerHTML += `<div class="cq-header"><div class="cq-title">${esc(quality.title)}</div><div class="cq-subtitle">${esc(quality.subtitle)}</div></div>`;
    }
    if (quality.dimensions && quality.dimensions.length) {
        let dimsHtml = '<div class="cq-dimensions">';
        quality.dimensions.forEach(d => {
            const color = d.color || '#e07850';
            dimsHtml += `<div class="cq-dimension"><div class="cq-dim-header"><span class="cq-dim-name" style="color:${color}">${esc(d.name)}</span><span class="cq-dim-pct" style="color:${color}">${d.percentage}%</span></div><div class="cq-dim-bar"><div class="cq-dim-fill" style="width:${d.percentage}%;background:${color}"></div></div><div class="cq-dim-comment">${esc(d.comment)}</div></div>`;
        });
        dimsHtml += '</div>';
        qualityDiv.innerHTML += dimsHtml;
    }
    if (quality.summary) {
        qualityDiv.innerHTML += `<div class="cq-summary">${esc(quality.summary)}</div>`;
    }
    insightEl.style.display = 'block';
}

async function loadDashboardInsight() {
    if (!currentGroup) return;
    const cached = _insightCacheGet(currentGroup);
    if (cached) { renderDashboardInsight(cached); return; }
    try {
        const res = await api('/api/analysis/ai', { method:'POST', body:JSON.stringify({group_name:currentGroup, use_rules:true, skip_report:true}) });
        if (res && res.success && res.data) {
            _insightCacheSet(currentGroup, res.data);
            renderDashboardInsight(res.data);
        } else {
            document.getElementById('dashboardInsight').style.display = 'none';
        }
    } catch(e) {
        document.getElementById('dashboardInsight').style.display = 'none';
    }
}

// H1/H4 修复：dashboard insight 区域用 IntersectionObserver 在进入视口时才请求，
// 避免首屏必跑全量 jieba 分词 + 关键词打分。
let _insightObserver = null;
function loadDashboardInsightOnView() {
    const el = document.getElementById('dashboardInsight');
    if (!el || _insightObserver) return;
    if (typeof IntersectionObserver === 'undefined') {
        // 浏览器不支持 IntersectionObserver，直接退化为按钮触发
        renderInsightPlaceholder();
        return;
    }
    _insightObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting && currentGroup) {
                _insightObserver.disconnect();
                _insightObserver = null;
                loadDashboardInsight();
            }
        });
    }, { rootMargin: '120px' });
    _insightObserver.observe(el);
}

function renderInsightPlaceholder() {
    // H4 修复：用户没点 AI 分析按钮 / 还没进入视口时，显示占位按钮
    const el = document.getElementById('dashboardInsight');
    if (!el) return;
    el.style.display = 'block';
    el.innerHTML = `
        <div class="chart-card insight-placeholder">
            <h3>💡 群聊洞察</h3>
            <p class="ai-summary-text">AI 洞察是相对耗时的分析任务，避免影响首屏加载速度。</p>
            <div class="insight-actions">
                <button id="btnGenerateInsight" class="btn btn-primary">点此生成 AI 洞察</button>
            </div>
        </div>`;
    const btn = document.getElementById('btnGenerateInsight');
    if (btn) btn.addEventListener('click', () => {
        btn.disabled = true;
        btn.textContent = '生成中...';
        loadDashboardInsight();
    });
}

let _currentHtmlFile = '';
/**
 * 显示 AI 失败警告横幅。
 * warnings: [{section, label, reason}, ...]
 */
function showAiWarnings(warnings) {
    const banner = document.getElementById('aiWarningsBanner');
    const list = banner && banner.querySelector('.ai-warnings-list');
    if (!banner || !list) return;
    list.innerHTML = '';
    if (Array.isArray(warnings) && warnings.length > 0) {
        warnings.forEach(w => {
            const li = document.createElement('li');
            const label = w && w.label ? w.label : '未知 section';
            const reason = w && w.reason ? w.reason : 'AI 未返回该字段';
            li.textContent = `${label}：${reason}`;
            list.appendChild(li);
        });
        banner.style.display = 'flex';
    } else {
        banner.style.display = 'none';
    }
}

function showHtmlPreview(htmlUrl, htmlFile, warnings) {
    const card = document.getElementById('htmlPreviewCard');
    const frame = document.getElementById('htmlPreviewFrame');
    const link = document.getElementById('htmlPreviewLink');
    const imgBtn = document.getElementById('btnGenerateImage');
    if (!card || !frame || !htmlUrl) return;
    // 防御性检查：url 必须是 /api/reports/download 路径，
    // 否则后端可能返回 JSON 错误被当成 HTML 加载
    if (!htmlUrl.startsWith('/api/reports/download')) {
        console.warn('showHtmlPreview: 非法的 html_url，跳过预览', htmlUrl);
        showToast('报告预览 URL 异常，请重试', 'error');
        return;
    }
    // 显式渲染 warnings banner（让用户看到 AI 部分失败的 section + 原因）
    showAiWarnings(warnings);
    frame.src = htmlUrl;
    link.href = htmlUrl;
    _currentHtmlFile = htmlFile || '';
    if (imgBtn) imgBtn.style.display = _currentHtmlFile ? 'inline-flex' : 'none';
    card.style.display = 'block';
}

/**
 * 第二步：从已渲染的 HTML 提交截图任务，返回 task_id 后由前端订阅 SSE。
 * 弹简化进度模态框（只显示 screenshot 阶段），完成时回调 onSuccess(result)。
 */
async function generateImageFromHtml(htmlPath, opts = {}) {
    if (!htmlPath) { showToast('没有可用的 HTML 报告', 'error'); return; }
    showScreenshotProgress();
    const res = await api('/api/report/image/screenshot/submit', {
        method: 'POST',
        body: JSON.stringify({ html_path: htmlPath, fmt: opts.fmt || 'jpg' }),
    });
    if (res && res.success && res.task_id) {
        subscribeReportProgress(res.task_id, {
            onSuccess: (result) => {
                hideReportProgress();
                if (result.image_url) {
                    showReportResult({ image_url: result.image_url, html_file: result.html_path });
                    showToast('图片已生成！', 'success');
                } else {
                    showToast('截图任务完成，但未返回 image_url', 'error');
                }
                opts.onSuccess?.(result);
            },
            onError: (err) => {
                hideReportProgress();
                showReportError(err);
                opts.onError?.(err);
            },
        });
        return res.task_id;
    }
    hideReportProgress();
    showToast('提交失败: ' + (res?.error || '未知错误'), 'error');
    opts.onError?.({ code: 'SUBMIT_FAILED', message: res?.error || '未知错误' });
    return null;
}

function hideHtmlPreview() {
    const card = document.getElementById('htmlPreviewCard');
    const frame = document.getElementById('htmlPreviewFrame');
    if (card) card.style.display = 'none';
    if (frame) frame.src = '';
}

let _idePollTimer = null;
function pollIdeTaskResult(taskId) {
    if (_idePollTimer) {
        clearTimeout(_idePollTimer);
        _idePollTimer = null;
    }
    // 优化 2 (AC2)：前 5 次 500ms 快轮询（覆盖 IDE AI 处理 2-5s 完成的情况），
    // 之后 1s→2s→4s→8s 指数退避，总时长上限 3 分钟
    // 1+0.5*5 + 1+2+4 + 8*15 ≈ 2.5 + 7 + 120 ≈ 129s，最坏 3 分钟
    const startTime = Date.now();
    const maxDurationMs = 3 * 60 * 1000;
    const maxDelay = 8000;
    let currentDelay = 500;  // 优化：起步从 1s → 500ms
    let fastPollCount = 0;
    const FAST_POLL_TIMES = 5;  // 前 5 次用 fast 间隔
    const FAST_DELAY = 500;

    const tick = async () => {
        if (Date.now() - startTime > maxDurationMs) {
            _idePollTimer = null;
            document.getElementById('ideTaskStatus').style.display = 'none';
            document.getElementById('aiEmpty').style.display = 'flex';
            showToast('分析超时，请稍后在报告历史中查看', 'error');
            return;
        }
        try {
            const res = await api(`/api/ide/task?task_id=${taskId}`);
            if (res && res.success && res.task) {
                const task = res.task;
                if (task.status === 'completed' && task.result) {
                    _idePollTimer = null;
                    document.getElementById('ideTaskStatus').style.display = 'none';
                    document.getElementById('aiResults').style.display = 'block';
                    if (task.report && task.report.html_url) {
                        showHtmlPreview(task.report.html_url, task.report.html_file, task.report.warnings);
                    }
                    if (task.report && task.report.image_url) {
                        showReportResult(task.report);
                    }
                    showToast('AI 分析完成，报告已生成', 'success');
                    return;
                } else if (task.status === 'failed') {
                    _idePollTimer = null;
                    document.getElementById('ideTaskStatus').style.display = 'none';
                    document.getElementById('aiEmpty').style.display = 'flex';
                    showToast('分析失败: ' + (task.result?.error || '未知错误'), 'error');
                    return;
                }
            }
        } catch(e) {
            console.error('IDE task poll error:', e);
        }
        if (_idePollTimer === null) return; // 已被新调用或终端态清理
        const d = currentDelay;
        // 优化 2 (AC2)：前 5 次固定 500ms 快轮询，之后进入指数退避
        if (fastPollCount < FAST_POLL_TIMES) {
            fastPollCount++;
            currentDelay = FAST_DELAY;
        } else {
            currentDelay = Math.min(currentDelay * 2, maxDelay);
        }
        _idePollTimer = setTimeout(tick, d);
    };

    _idePollTimer = setTimeout(tick, currentDelay);
    if (fastPollCount < FAST_POLL_TIMES) {
        fastPollCount++;
    } else {
        currentDelay = Math.min(currentDelay * 2, maxDelay);
    }
}

async function loadGroups() {
    const res = await api('/api/groups');
    const select = document.getElementById('groupSelect');
    const current = select.value;
    select.innerHTML = '<option value="">选择群聊</option>';
    if (res && res.group_info) {
        window._allGroups = res.group_info;
        res.group_info.forEach(g => {
            const opt = document.createElement('option');
            opt.value = g.value;
            opt.textContent = g.label !== g.value ? g.label : g.value;
            if (g.value === current) opt.selected = true;
            select.appendChild(opt);
        });
    } else if (res && res.groups) {
        res.groups.forEach(g => {
            const opt = document.createElement('option');
            opt.value = g; opt.textContent = g;
            if (g === current) opt.selected = true;
            select.appendChild(opt);
        });
    }
    return res;
}

async function loadDataFiles() {
    const res = await api('/api/data-files');
    const container = document.getElementById('dataFiles');
    const btnDeleteAll = document.getElementById('btnDeleteAllData');
    const btnExportCSV = document.getElementById('btnExportCSV');
    if (!res || !res.files || !res.files.length) {
        container.innerHTML = '<p class="empty-hint">暂无已加载数据</p>';
        if (btnDeleteAll) btnDeleteAll.style.display = 'none';
        if (btnExportCSV) btnExportCSV.style.display = 'none';
        return;
    }
    if (btnDeleteAll) btnDeleteAll.style.display = 'inline-flex';
    if (btnExportCSV) btnExportCSV.style.display = 'inline-flex';
    // F16: 容器事件委托，避免每行重建 DOM 都重新 addEventListener
    if (!container._delegated) {
        container._delegated = true;
        container.addEventListener('click', async e => {
            const t = e.target.closest('[data-action]');
            if (!t) return;
            if (t.dataset.action === 'select-data') {
                const g = t.dataset.group;
                if (!g) return;
                document.getElementById('groupSelect').value = g;
                currentGroup = g;
                loadStats();
            } else if (t.dataset.action === 'delete-data') {
                e.stopPropagation();
                const g = t.dataset.group;
                if (!g) return;
                if (!confirm(`确定要删除 "${g}" 的数据吗？`)) return;
                const delRes = await api('/api/data/delete', { method:'DELETE', body:JSON.stringify({group_name:g}) });
                if (delRes && delRes.success) {
                    showToast('已删除: ' + g, 'success');
                    if (currentGroup === g) { currentGroup = ''; analysisData = null; updateWelcomeGuide(); }
                    loadDataFiles(); loadGroups();
                } else {
                    showToast('删除失败: '+(delRes?.error||'未知错误'), 'error');
                }
            }
        });
    }
    container.innerHTML = '';
    res.files.forEach(f => {
        const div = document.createElement('div');
        div.className = 'data-file-item';
        div.innerHTML = `<div class="df-info-wrap" data-action="select-data" data-group="${esc(f.group_name)}"><div class="df-name">${esc(f.group_name)}</div><div class="df-info">加载时间: ${esc(f.collected_at)||'-'}</div></div><div class="df-actions"><span class="df-count">${f.message_count} 条</span><button class="btn btn-icon btn-delete" data-action="delete-data" data-group="${esc(f.group_name)}" title="删除"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></div>`;
        container.appendChild(div);
    });
}

// F10: 5s 内复用 /api/status 结果（checkStatus 与 loadConfig 都会调）
let _statusCache = null, _statusCacheTs = 0;
const _STATUS_TTL = 5000;
async function getStatusCached() {
    if (_statusCache && Date.now() - _statusCacheTs < _STATUS_TTL) return _statusCache;
    _statusCache = await api('/api/status');
    _statusCacheTs = Date.now();
    return _statusCache;
}
async function checkStatus() {
    showGlobalLoading();
    const res = await getStatusCached();
    const chatlogEl = document.getElementById('chatlogStatus');
    const clBadge = document.getElementById('chatlogBadge');
    const clBar = document.getElementById('chatlogStatusBar');
    const clActions = document.getElementById('chatlogActions');
    const clGuide = document.getElementById('chatlogInstallGuide');
    const mcpEl = document.getElementById('mcpStatus');
    const aiEl = document.getElementById('aiStatus');

    if (res && res.chatlog_available) {
        chatlogEl.innerHTML = '<span class="status-dot active"></span><span class="status-text">chatlog: 可用</span>';
        if (clBadge) { clBadge.className = 'badge online'; clBadge.textContent = '可用'; }
        if (clBar) clBar.innerHTML = '<span class="status-dot active"></span><span>chatlog 数据库已连接 (' + (res.chatlog_talkers_count||0) + ' 个聊天)</span>';
        if (clActions) clActions.style.display = 'flex';
        if (clGuide) clGuide.style.display = 'none';
        loadChatlogTalkers();
    } else {
        chatlogEl.innerHTML = '<span class="status-dot inactive"></span><span class="status-text">chatlog: 不可用</span>';
        if (clBadge) { clBadge.className = 'badge offline'; clBadge.textContent = '不可用'; }
        if (clBar) clBar.innerHTML = '<span class="status-dot inactive"></span><span>chatlog 数据库未找到</span>';
        if (clActions) clActions.style.display = 'none';
        if (clGuide) clGuide.style.display = 'block';
    }

    if (mcpEl) {
        if (res && res.mcp_available) {
            mcpEl.innerHTML = '<span class="status-dot active"></span><span class="status-text">MCP: 已就绪</span>';
        } else {
            mcpEl.innerHTML = '<span class="status-dot inactive"></span><span class="status-text">MCP: 未连接</span>';
        }
    }

    if (aiEl) {
        if (res && res.ai_configured) {
            const provider = res.ai_provider || 'deepseek';
            const providerNames = { deepseek: 'DeepSeek', openai: 'OpenAI', ollama: 'Ollama' };
            const name = providerNames[provider] || provider;
            aiEl.innerHTML = '<span class="status-dot active"></span><span class="status-text">AI: ' + name + '</span>';
        } else {
            aiEl.innerHTML = '<span class="status-dot inactive"></span><span class="status-text">AI: 未配置</span>';
        }
    }

    // API Key 占位符引导横幅
    const apikeyBanner = document.getElementById('apikeyBanner');
    if (apikeyBanner) {
        if (res && res.ai_key_placeholder) {
            apikeyBanner.style.display = 'flex';
        } else {
            apikeyBanner.style.display = 'none';
        }
    }

    hideGlobalLoading();
    return res;
}

async function loadChatlogTalkers() {
    const res = await api('/api/chatlog/talkers');
    const select = document.getElementById('chatlogTalkerSelect');
    select.innerHTML = '<option value="">选择聊天对象</option>';
    if (res && res.success && res.talkers) {
        res.talkers.forEach(t => {
            const opt = document.createElement('option');
            opt.value = t.talker;
            const label = t.display_name || t.talker;
            const isGroup = t.talker.includes('@chatroom');
            opt.textContent = `${isGroup?'[群]':'[私]'} ${label} (${t.message_count}条)`;
            select.appendChild(opt);
        });
    }
}

async function loadReportHistory() {
    const container = document.getElementById('reportHistoryList');
    showGlobalLoading();
    try {
        const res = await api('/api/reports');
        if (!res || !res.reports || !res.reports.length) {
            container.innerHTML = '<p class="empty-hint">暂无报告记录</p>';
            return;
        }
        // F16: 容器事件委托，避免每行重建 DOM 都重新 addEventListener
        if (!container._delegated) {
            container._delegated = true;
            container.addEventListener('click', async e => {
                const t = e.target.closest('[data-action]');
                if (!t) return;
                if (t.dataset.action === 'preview-report') {
                    e.stopPropagation();
                    const url = t.dataset.url;
                    const format = t.dataset.format;
                    if (!url) { showToast('报告链接无效','error'); return; }
                    if (format === 'HTML') {
                        switchPage('ai');
                        showHtmlPreview(url);
                    } else {
                        window.open(url, '_blank');
                    }
                } else if (t.dataset.action === 'delete-report') {
                    e.stopPropagation();
                    const filename = t.dataset.filename;
                    if (!filename) return;
                    if (!confirm('确定要删除这份报告吗？')) return;
                    const delRes = await api('/api/reports/delete', { method:'DELETE', body:JSON.stringify({filename}) });
                    if (delRes && delRes.success) {
                        showToast('报告已删除', 'success');
                        loadReportHistory();
                    } else {
                        showToast('删除失败: '+(delRes?.error||'未知错误'), 'error');
                    }
                }
            });
        }
        container.innerHTML = '';
        res.reports.forEach(r => {
            const div = document.createElement('div');
            div.className = 'data-file-item';
            const fmt = (r.format || '').toUpperCase();
            const formatLabel = fmt || '未知';
            const formatColor = fmt === 'HTML' ? 'var(--accent)' : fmt === 'JPG' || fmt === 'PNG' ? '#d4a04a' : fmt === 'PDF' ? '#5a9eaf' : 'var(--text-muted)';
            div.innerHTML = `<div class="df-info-wrap"><div class="df-name">${esc(r.group_name || '未知群聊')}</div><div class="df-info">${esc(r.created_at) || '-'} · <span style="color:${formatColor};font-weight:600">${formatLabel}</span> · ${r.size_kb || 0}KB</div></div><div class="df-actions"><button class="btn btn-outline btn-sm btn-preview-report" data-action="preview-report" data-url="${r.url || ''}" data-format="${fmt}">查看</button><a href="${r.url || '#'}" target="_blank" class="btn btn-outline btn-sm" title="新窗口打开"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a><button class="btn btn-icon btn-delete" data-action="delete-report" data-filename="${esc(r.filename)}" title="删除"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></div>`;
            container.appendChild(div);
        });
    } catch(e) {
        container.innerHTML = '<p class="empty-hint">暂无报告记录</p>';
    } finally {
        hideGlobalLoading();
    }
}

async function loadConfig() {
    const [res, statusRes] = await Promise.all([api('/api/config'), getStatusCached()]);
    if (res && res.success) {
        const cfg = res.config.ai_service;
        document.getElementById('cfgProvider').value = cfg.provider || 'deepseek';
        document.getElementById('cfgApiKey').value = '';
        document.getElementById('cfgApiKey').placeholder = cfg.api_key_set ? cfg.api_key : 'sk-...';
        const hint = document.getElementById('cfgApiKeyHint');
        if (hint) {
            if (cfg.api_key_placeholder) {
                hint.textContent = '⚠️ API Key 为占位符，请替换为有效密钥';
                hint.className = 'form-hint hint-warn';
            } else if (cfg.api_key_set) {
                hint.textContent = '✅ API Key 已配置 (' + cfg.api_key + ')';
                hint.className = 'form-hint hint-ok';
            } else {
                hint.textContent = '⚠️ 未配置 API Key，将使用规则分析';
                hint.className = 'form-hint hint-warn';
            }
        }
        document.getElementById('cfgBaseUrl').value = cfg.base_url || '';
        document.getElementById('cfgModel').value = cfg.model || '';
        document.getElementById('cfgTemp').value = cfg.temperature || 0.7;
        document.getElementById('cfgMaxTokens').value = cfg.max_tokens || 4096;
        const cwEl = document.getElementById('cfgConcurrentWorkers');
        if (cwEl) {
            const cw = parseInt(cfg.concurrent_workers) || 5;
            cwEl.value = String(Math.min(5, Math.max(1, cw)));
        }
        const etEl = document.getElementById('cfgEnableThinking');
        if (etEl) {
            etEl.checked = cfg.enable_thinking !== false;  // 默认 true
        }
    }
    const mcpBar = document.getElementById('mcpStatusBar');
    if (mcpBar && statusRes) {
        if (statusRes.mcp_available) {
            mcpBar.innerHTML = '<span class="status-dot active"></span><span>MCP 服务器模块已就绪，可在 IDE 中配置使用</span>';
        } else {
            mcpBar.innerHTML = '<span class="status-dot inactive"></span><span>MCP 服务器模块未加载</span>';
        }
    }
    loadScheduleGroups();
    loadScheduleList();
}

function initRangePicker() {
    // 默认设置为近1天
    const now = new Date();
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1 + 1); // 今天
    document.getElementById('rangeStartDate').value = yesterday.toISOString().slice(0,10);
    document.getElementById('rangeEndDate').value = now.toISOString().slice(0,10);

    const quickBtns = document.querySelectorAll('.range-quick-btns .btn');
    quickBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const range = btn.dataset.range;
            const end = new Date();
            const startInput = document.getElementById('rangeStartDate');
            const endInput = document.getElementById('rangeEndDate');
            if (range === 'all') {
                startInput.value = '';
                endInput.value = '';
            } else {
                const days = parseInt(range);
                const start = new Date(end);
                start.setDate(start.getDate() - days + 1);
                startInput.value = start.toISOString().slice(0,10);
                endInput.value = end.toISOString().slice(0,10);
            }
        });
    });
    document.getElementById('btnRangeClear').addEventListener('click', () => {
        document.getElementById('rangeStartDate').value = '';
        document.getElementById('rangeEndDate').value = '';
    });
}

function switchPage(page) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const pageEl = document.getElementById('page-' + page);
    if (pageEl) pageEl.classList.add('active');
    const navEl = document.querySelector(`.nav-item[data-page="${page}"]`);
    if (navEl) navEl.classList.add('active');
    const titles = { dashboard:'数据概览', ai:'AI 分析', datasource:'数据源', reports:'报告历史', settings:'设置' };
    document.getElementById('pageTitle').textContent = titles[page] || '';
    if (page === 'reports') loadReportHistory();
    if (page === 'settings') loadConfig();
}

async function loadScheduleGroups() {
    const cached = window._allGroups;
    const [res, groupsRes] = await Promise.all([api('/api/chatlog/talkers'), cached ? Promise.resolve(null) : api('/api/groups')]);
    const select = document.getElementById('schedGroupSelect');
    if (!select) return;
    const current = select.value;
    select.innerHTML = '<option value="">选择群聊</option>';
    if (res && res.success && res.talkers) {
        res.talkers.filter(t => t.talker.includes('@chatroom')).forEach(t => {
            const opt = document.createElement('option');
            opt.value = t.talker;
            opt.textContent = `${t.display_name || t.talker} (${t.message_count}条)`;
            if (t.talker === current) opt.selected = true;
            select.appendChild(opt);
        });
    }
    const info = (groupsRes && groupsRes.group_info) || cached || [];
    if (info.length) {
        const existing = new Set(Array.from(select.options).map(o => o.value));
        info.filter(g => !existing.has(g.value)).forEach(g => {
            const opt = document.createElement('option');
            opt.value = g.value;
            opt.textContent = g.label || g.value;
            if (g.value === current) opt.selected = true;
            select.appendChild(opt);
        });
    }
}

let _schedPollTimer = null;
async function loadScheduleList() {
    const container = document.getElementById('scheduleList');
    if (!container) return;
    const res = await api('/api/schedule/list');
    if (!res || !res.success || !res.tasks || !res.tasks.length) {
        container.innerHTML = '<p class="empty-hint">暂无定时任务</p>';
        if (_schedPollTimer) { clearInterval(_schedPollTimer); _schedPollTimer = null; }
        return;
    }
    container.innerHTML = '';
    let hasRunning = false;
    res.tasks.forEach(t => {
        if (t.status === 'running') hasRunning = true;
        const div = document.createElement('div');
        div.className = 'data-file-item';
        const statusMap = { idle: '等待中', running: '执行中', completed: '已完成', failed: '失败', timeout: '超时' };
        const statusColor = { idle: 'var(--text-muted)', running: '#e88d5c', completed: '#5ea87a', failed: '#c47a8a', timeout: '#d4a04a' };
        const enabledLabel = t.enabled ? '✅ 已启用' : '⏸️ 已禁用';
        const enabledColor = t.enabled ? '#5ea87a' : 'var(--text-muted)';
        let errorHtml = '';
        if (t.last_result && t.last_result.error) {
            errorHtml = `<div style="color:#c47a8a;font-size:12px;margin-top:4px;padding:4px 8px;background:rgba(196,122,138,0.08);border-radius:4px">❌ ${esc(t.last_result.error)}</div>`;
        }
        let historyHtml = '';
        const history = t.history || [];
        if (history.length > 0) {
            historyHtml = '<div style="margin-top:6px;font-size:12px;color:var(--text-muted)">';
            historyHtml += '<div style="font-weight:600;margin-bottom:2px">📜 执行记录</div>';
            history.slice(0, 5).forEach(h => {
                const icon = h.success ? '✅' : '❌';
                const methodLabel = h.method ? ` (${h.method})` : '';
                const errPart = h.error ? ` - ${esc(h.error)}` : '';
                historyHtml += `<div>${icon} ${esc(h.time)}${methodLabel}${errPart}</div>`;
            });
            historyHtml += '</div>';
        }
        div.innerHTML = `<div class="df-info-wrap"><div class="df-name">${esc(t.group_name)}</div><div class="df-info">每天 ${String(t.hour).padStart(2,'0')}:${String(t.minute).padStart(2,'0')} · <span style="color:${enabledColor}">${enabledLabel}</span> · <span style="color:${statusColor[t.status]||'var(--text-muted)'}">${statusMap[t.status]||t.status}</span>${t.last_run ? ' · 上次执行: '+esc(t.last_run) : ''}</div>${errorHtml}${historyHtml}</div><div class="df-actions"><button class="btn btn-outline btn-sm btn-sched-toggle" data-id="${esc(t.task_id)}" data-enabled="${t.enabled?'false':'true'}">${t.enabled?'禁用':'启用'}</button><button class="btn btn-outline btn-sm btn-sched-trigger" data-id="${esc(t.task_id)}">▶ 立即执行</button><button class="btn btn-icon btn-delete btn-sched-delete" data-id="${esc(t.task_id)}" title="删除"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></div>`;
        div.querySelector('.btn-sched-toggle').addEventListener('click', async (e) => {
            const id = e.currentTarget.dataset.id;
            const enabled = e.currentTarget.dataset.enabled === 'true';
            const r = await api('/api/schedule/toggle', { method:'POST', body:JSON.stringify({task_id:id, enabled}) });
            if (r && r.success) { showToast(r.message, 'success'); loadScheduleList(); }
            else showToast('操作失败: '+(r?.error||''), 'error');
        });
        div.querySelector('.btn-sched-trigger').addEventListener('click', async (e) => {
            const id = e.currentTarget.dataset.id;
            const r = await api('/api/schedule/trigger', { method:'POST', body:JSON.stringify({task_id:id}) });
            if (r && r.success) { showToast('已触发执行，请稍后查看报告历史', 'success'); loadScheduleList(); }
            else showToast('触发失败: '+(r?.error||''), 'error');
        });
        div.querySelector('.btn-sched-delete').addEventListener('click', async (e) => {
            const id = e.currentTarget.dataset.id;
            if (!confirm('确定要删除这个定时任务吗？')) return;
            const r = await api('/api/schedule/delete', { method:'DELETE', body:JSON.stringify({task_id:id}) });
            if (r && r.success) { showToast('已删除', 'success'); loadScheduleList(); }
            else showToast('删除失败: '+(r?.error||''), 'error');
        });
        container.appendChild(div);
    });
    if (hasRunning) {
        if (!_schedPollTimer) {
            _schedPollTimer = setInterval(() => loadScheduleList(), 5000);
        }
    } else {
        if (_schedPollTimer) { clearInterval(_schedPollTimer); _schedPollTimer = null; }
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', e => { e.preventDefault(); switchPage(item.dataset.page); });
    });

    document.getElementById('groupSelect').addEventListener('change', e => {
        currentGroup = e.target.value;
        if (currentGroup) {
            loadStats();
        } else {
            analysisData = null;
            updateWelcomeGuide();
        }
    });

    document.getElementById('btnRefresh').addEventListener('click', () => {
        if (currentGroup) loadStats();
        else showToast('请先选择群聊','error');
    });

    document.getElementById('btnChatlogLoad').addEventListener('click', async () => {
        const talker = document.getElementById('chatlogTalkerSelect').value;
        const limit = parseInt(document.getElementById('chatlogLimit').value) || 0;
        if (!talker) { showToast('请选择聊天对象','error'); return; }
        showToast('正在从 chatlog 加载数据...','info');
        const res = await api('/api/chatlog/load', { method:'POST', body:JSON.stringify({talker,limit}) });
        if (res && res.success) {
            showToast(`加载完成: ${res.message_count} 条消息`,'success');
            currentGroup = talker;
            const displayName = document.getElementById('chatlogTalkerSelect').selectedOptions[0]?.textContent || talker;
            const opt = document.createElement('option');
            opt.value = talker; opt.textContent = displayName; opt.selected = true;
            const sel = document.getElementById('groupSelect');
            const existing = Array.from(sel.options).find(o => o.value === talker);
            if (!existing) sel.appendChild(opt);
            else existing.selected = true;
            loadGroups(); loadDataFiles(); loadStats();
            switchPage('dashboard');
        } else { showToast('加载失败: '+(res?.error||'未知错误'),'error'); }
    });

    document.getElementById('btnChatlogRefresh').addEventListener('click', async () => {
        const btn = document.getElementById('btnChatlogRefresh');
        btn.disabled = true; btn.textContent = '正在刷新...';
        showToast('正在重新解密微信数据库，请稍候...','info');
        const res = await api('/api/chatlog/refresh');
        btn.disabled = false;
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>刷新微信数据（重新解密）';
        if (res && res.success) {
            showToast('数据刷新成功！','success');
            loadChatlogTalkers(); loadDataFiles();
        } else {
            showToast('刷新失败: '+(res?.error||'请确认微信正在运行'),'error');
        }
    });

    document.getElementById('btnRetryDetect').addEventListener('click', async () => {
        const btn = document.getElementById('btnRetryDetect');
        btn.disabled = true; btn.textContent = '检测中...';
        await checkStatus();
        btn.disabled = false;
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>重新检测';
    });

    document.getElementById('btnAiAnalysis').addEventListener('click', async () => {
        if (!currentGroup) { showToast('请先选择群聊','error'); return; }
        doAutoAnalysis();
    });

    document.getElementById('apikeyBannerBtn').addEventListener('click', () => {
        switchPage('settings');
    });

    document.querySelectorAll('.theme-card').forEach(card => {
        card.addEventListener('click', () => {
            document.querySelectorAll('.theme-card').forEach(c => c.classList.remove('active'));
            card.classList.add('active');
            showThemePreview(card.dataset.theme);
        });
    });

    // 分析模式下拉菜单
    const btnAnalysisDropdown = document.getElementById('btnAnalysisDropdown');
    const analysisDropdownMenu = document.getElementById('analysisDropdownMenu');
    if (btnAnalysisDropdown && analysisDropdownMenu) {
        btnAnalysisDropdown.addEventListener('click', (e) => {
            e.stopPropagation();
            analysisDropdownMenu.classList.toggle('show');
        });
        analysisDropdownMenu.querySelectorAll('.dropdown-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.stopPropagation();
                const mode = item.dataset.mode;
                document.getElementById('btnAiAnalysis').dataset.mode = mode;
                analysisDropdownMenu.querySelectorAll('.dropdown-item').forEach(i => i.classList.remove('active'));
                item.classList.add('active');
                analysisDropdownMenu.classList.remove('show');
            });
        });
        document.addEventListener('click', () => {
            analysisDropdownMenu.classList.remove('show');
        });
    }

    // 报告风格预览弹窗
    const themePreviewModal = document.getElementById('themePreviewModal');
    const themePreviewClose = document.getElementById('themePreviewClose');
    const themePreviewApply = document.getElementById('themePreviewApply');
    if (themePreviewClose) {
        themePreviewClose.addEventListener('click', () => {
            themePreviewModal.style.display = 'none';
        });
    }
    if (themePreviewApply) {
        themePreviewApply.addEventListener('click', () => {
            themePreviewModal.style.display = 'none';
        });
    }
    if (themePreviewModal) {
        themePreviewModal.addEventListener('click', (e) => {
            if (e.target === themePreviewModal) themePreviewModal.style.display = 'none';
        });
    }

    // 两步流程：HTML 预览后的"生成图片"按钮
    const onGenerateImageClick = async () => {
        if (!_currentHtmlFile) { showToast('没有可用的 HTML 报告','error'); return; }
        const btns = [
            document.getElementById('btnGenerateImage'),
        ].filter(Boolean);
        btns.forEach(b => { b.disabled = true; b.textContent = '⏳ 生成中...'; });
        // 第二步：调 /api/report/image/screenshot/submit，弹进度模态框 + SSE 订阅
        showScreenshotProgress();
        try {
            const res = await api('/api/report/image/screenshot/submit', {
                method:'POST',
                body:JSON.stringify({html_path: _currentHtmlFile, fmt:'jpg'}),
            });
            if (res && res.success && res.task_id) {
                subscribeReportProgress(res.task_id, {
                    onSuccess: (result) => {
                        hideReportProgress();
                        if (result.image_url) {
                            showReportResult({ image_url: result.image_url, html_file: result.html_path });
                            showToast('图片已生成！', 'success');
                        } else {
                            showToast('截图任务完成，但未返回 image_url', 'error');
                        }
                        btns.forEach(b => { b.disabled = false; b.textContent = '🖼️ 生成图片'; });
                    },
                    onError: (err) => {
                        hideReportProgress();
                        showReportError(err);
                        btns.forEach(b => { b.disabled = false; b.textContent = '🖼️ 生成图片'; });
                    },
                });
            } else {
                hideReportProgress();
                showToast('提交失败: ' + (res?.error || '未知错误'), 'error');
                btns.forEach(b => { b.disabled = false; b.textContent = '🖼️ 生成图片'; });
            }
        } catch (e) {
            hideReportProgress();
            showToast('请求异常: ' + (e?.message || e), 'error');
            btns.forEach(b => { b.disabled = false; b.textContent = '🖼️ 生成图片'; });
        }
    };
    const btnGenImg = document.getElementById('btnGenerateImage');
    if (btnGenImg) btnGenImg.addEventListener('click', onGenerateImageClick);

    document.getElementById('welcomeStep1').addEventListener('click', () => {
        switchPage('datasource');
    });

    document.getElementById('btnDeleteAllData').addEventListener('click', async () => {
        const res = await api('/api/data-files');
        if (!res || !res.files || !res.files.length) { showToast('没有可删除的数据','info'); return; }
        if (!confirm(`确定要清除全部 ${res.files.length} 条数据吗？此操作不可恢复！`)) return;
        const delRes = await api('/api/data/batch-delete', { method:'POST', body:JSON.stringify({group_names: res.files.map(f => f.group_name)}) });
        currentGroup = '';
        analysisData = null;
        loadDataFiles(); loadGroups();
        updateWelcomeGuide();
        const deleted = delRes?.deleted ?? 0, failed = delRes?.failed ?? 0;
        showToast(`已删除 ${deleted} 条数据${failed?'，'+failed+' 条失败':''}`, failed?'error':'success');
    });

    document.getElementById('btnExportCSV').addEventListener('click', () => {
        if (!currentGroup) { showToast('请先选择群聊', 'error'); return; }
        window.open(`/api/data/export?group=${encodeURIComponent(currentGroup)}&fmt=csv`, '_blank');
    });

    // ── 多群对比 ──────────────────────────────────────────
    let compareGroups = [];
    const compareColors = ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#06b6d4', '#ec4899'];

    function loadCompareGroups() {
        const container = document.getElementById('compareGroupList');
        if (!container) return;
        const groups = window._allGroups || [];
        container.innerHTML = '';
        if (!groups.length) {
            container.innerHTML = '<p class="empty-hint">暂无群聊数据，请先加载数据</p>';
            return;
        }
        groups.forEach(g => {
            const label = g.label || g.value || g;
            const value = g.value || g;
            const div = document.createElement('div');
            div.className = 'compare-group-item' + (compareGroups.includes(value) ? ' selected' : '');
            div.innerHTML = `<input type="checkbox" ${compareGroups.includes(value) ? 'checked' : ''}><span>${esc(label)}</span>`;
            div.addEventListener('click', () => {
                if (compareGroups.includes(value)) {
                    compareGroups = compareGroups.filter(x => x !== value);
                    div.classList.remove('selected');
                    div.querySelector('input').checked = false;
                } else {
                    if (compareGroups.length >= 6) { showToast('最多选择 6 个群聊', 'error'); return; }
                    compareGroups.push(value);
                    div.classList.add('selected');
                    div.querySelector('input').checked = true;
                }
                const btn = document.getElementById('btnCompare');
                if (btn) btn.disabled = compareGroups.length < 2;
            });
            container.appendChild(div);
        });
    }

    document.getElementById('btnCompare')?.addEventListener('click', async () => {
        if (compareGroups.length < 2) { showToast('至少选择 2 个群聊', 'error'); return; }
        const res = await api('/api/analysis/compare', { method: 'POST', body: JSON.stringify({ groups: compareGroups }) });
        if (!res || !res.success) { showToast(res?.error || '对比分析失败', 'error'); return; }
        renderCompareResult(res.comparisons);
    });

    function renderCompareResult(comparisons) {
        const resultDiv = document.getElementById('compareResult');
        const overviewDiv = document.getElementById('compareOverview');
        const chartsDiv = document.getElementById('compareCharts');
        if (!resultDiv || !overviewDiv || !chartsDiv) return;
        resultDiv.style.display = 'block';
        const available = comparisons.filter(c => c.available);
        // 概览卡片
        overviewDiv.innerHTML = available.map((c, i) => `
            <div class="compare-card" style="border-top:3px solid ${compareColors[i % compareColors.length]}">
                <h3 style="color:${compareColors[i % compareColors.length]}">${esc(c.group_name)}</h3>
                <div class="compare-metric"><span class="label">消息总数</span><span class="value">${c.total_messages}</span></div>
                <div class="compare-metric"><span class="label">参与人数</span><span class="value">${c.total_members}</span></div>
                <div class="compare-metric"><span class="label">日均消息</span><span class="value">${c.avg_daily}</span></div>
                <div class="compare-metric"><span class="label">时间范围</span><span class="value">${c.time_start || '?'} ~ ${c.time_end || '?'}</span></div>
                ${c.keywords.length ? `<div style="margin-top:8px;font-size:12px;color:var(--text-secondary)">关键词: ${c.keywords.slice(0, 5).map(k => `<span style="background:var(--bg-input);padding:1px 6px;border-radius:3px;margin:0 2px">${esc(k)}</span>`).join('')}</div>` : ''}
            </div>
        `).join('');
        // 消息量对比条形图
        const maxMsg = Math.max(...available.map(c => c.total_messages)) || 1;
        chartsDiv.innerHTML = `
            <div class="compare-card" style="margin-top:16px">
                <h3>消息量对比</h3>
                ${available.map((c, i) => `
                    <div class="compare-bar-row">
                        <div class="compare-bar-label">${esc(c.group_name)}</div>
                        <div class="compare-bar-track">
                            <div class="compare-bar-fill" style="width:${(c.total_messages / maxMsg * 100).toFixed(1)}%;background:${compareColors[i % compareColors.length]}">${c.total_messages}</div>
                        </div>
                    </div>
                `).join('')}
            </div>
            <div class="compare-card" style="margin-top:12px">
                <h3>日均消息对比</h3>
                ${available.map((c, i) => {
                    const maxDaily = Math.max(...available.map(x => x.avg_daily)) || 1;
                    return `
                    <div class="compare-bar-row">
                        <div class="compare-bar-label">${esc(c.group_name)}</div>
                        <div class="compare-bar-track">
                            <div class="compare-bar-fill" style="width:${(c.avg_daily / maxDaily * 100).toFixed(1)}%;background:${compareColors[i % compareColors.length]}">${c.avg_daily}</div>
                        </div>
                    </div>`;
                }).join('')}
            </div>
            <div class="compare-card" style="margin-top:12px">
                <h3>氛围对比</h3>
                ${available.map((c, i) => {
                    const dims = c.vibe_dims || [];
                    return `<div style="margin-bottom:8px"><span style="font-size:13px;font-weight:600;color:${compareColors[i % compareColors.length]}">${esc(c.group_name)}</span>
                    ${dims.length ? dims.map(d => `<span style="font-size:12px;background:var(--bg-input);padding:1px 6px;border-radius:3px;margin:0 2px">${esc(d.name)} ${d.percentage}%</span>`).join('') : '<span style="font-size:12px;color:var(--text-muted)">数据不足</span>'}
                    </div>`;
                }).join('')}
            </div>
        `;
    }

    // 切换到对比页面时加载群列表
    const origSwitchPage = window.switchPage;
    window.switchPage = function(page) {
        if (typeof origSwitchPage === 'function') origSwitchPage(page);
        if (page === 'compare') loadCompareGroups();
    };

    document.getElementById('btnSaveConfig').addEventListener('click', async () => {
        const cfg = {
            ai_service: {
                provider: document.getElementById('cfgProvider').value,
                api_key: document.getElementById('cfgApiKey').value,
                base_url: document.getElementById('cfgBaseUrl').value,
                model: document.getElementById('cfgModel').value,
                temperature: parseFloat(document.getElementById('cfgTemp').value) || 0.7,
                max_tokens: parseInt(document.getElementById('cfgMaxTokens').value) || 4096,
                concurrent_workers: parseInt(document.getElementById('cfgConcurrentWorkers').value) || 5,
                enable_thinking: document.getElementById('cfgEnableThinking').checked,
            }
        };
        const res = await api('/api/config/save', { method: 'POST', body: JSON.stringify(cfg) });
        if (res && res.success) {
            showToast('配置已保存', 'success');
            document.getElementById('cfgApiKey').value = '';
            loadConfig();
            // 刷新 API Key 状态和引导横幅
            checkStatus();
        } else {
            showToast('保存失败: ' + (res?.error || '未知错误'), 'error');
        }
    });

    document.getElementById('btnTestConfig').addEventListener('click', async () => {
        const statusEl = document.getElementById('settingsStatus');
        statusEl.style.display = 'block';
        statusEl.className = 'settings-status testing';
        statusEl.textContent = '正在测试连接...';
        const res = await api('/api/analysis/ai', {
            method: 'POST',
            body: JSON.stringify({ group_name: currentGroup || '__test__', use_rules: true })
        });
        if (res && res.success) {
            statusEl.className = 'settings-status success';
            statusEl.textContent = '✅ 连接正常，规则分析可用';
        } else {
            statusEl.className = 'settings-status error';
            statusEl.textContent = '❌ ' + (res?.error || '连接失败');
        }
    });

    document.getElementById('cfgProvider').addEventListener('change', (e) => {
        const provider = e.target.value;
        const urlMap = { deepseek: 'https://api.deepseek.com/v1', openai: 'https://api.openai.com/v1', ollama: 'http://localhost:11434/v1' };
        const modelMap = { deepseek: 'deepseek-chat', openai: 'gpt-4o-mini', ollama: 'qwen2.5:7b' };
        document.getElementById('cfgBaseUrl').value = urlMap[provider] || '';
        document.getElementById('cfgModel').value = modelMap[provider] || '';
    });

    document.getElementById('btnCreateSchedule').addEventListener('click', async () => {
        const group_name = document.getElementById('schedGroupSelect').value;
        const hour = parseInt(document.getElementById('schedHour').value) || 9;
        const minute = parseInt(document.getElementById('schedMinute').value) || 0;
        const theme = document.getElementById('schedTheme').value;
        if (!group_name) { showToast('请选择群聊','error'); return; }
        if (hour < 0 || hour > 23 || minute < 0 || minute > 59) { showToast('时间格式错误','error'); return; }
        const res = await api('/api/schedule/create', { method:'POST', body:JSON.stringify({group_name, hour, minute, theme}) });
        if (res && res.success) {
            showToast(`定时任务已创建：每天 ${String(hour).padStart(2,'0')}:${String(minute).padStart(2,'0')} 分析`, 'success');
            loadScheduleList();
        } else {
            showToast('创建失败: '+(res?.error||'未知错误'), 'error');
        }
    });

    initRangePicker();

    // H4 修复：首屏 3 个独立 API 全部走 Promise.all，并行触发
    // 之前 loadDataFiles 没进 Promise.all，部分串行；loadStats 也不再自动触发 loadDashboardInsight
    const [statusRes, groupsRes, dataFilesRes] = await Promise.all([
        checkStatus(),
        loadGroups(),
        loadDataFiles(),
    ]);
    switchPage('dashboard');

    if (groupsRes && groupsRes.groups && groupsRes.groups.length > 0) {
        currentGroup = groupsRes.groups[0];
        document.getElementById('groupSelect').value = currentGroup;
        loadStats();
        // 注册 IntersectionObserver，dashboard insight 区域进入视口时才请求 AI 数据
        // 没点 AI 分析按钮前由 renderInsightPlaceholder 显示占位按钮
        renderInsightPlaceholder();
        loadDashboardInsightOnView();
    }
});
