"""播放/暂停来源诊断。

背景：日志里出现过「进度在涨、但播放器 currentTime 几乎不动」的情况，
说明视频被反复暂停。为了不再靠猜，这里给 HTMLMediaElement.prototype.pause/play
打上钩子，记录**是谁调用的**（调用栈），并把调用栈去重后写进日志。

只在配置 diagnostics.record_events = True 时安装，开销很小（每次 play/pause 记一条）。
"""

from __future__ import annotations

import json

from .logger import Logger

from . import paths

HOOK_JS = r"""
(() => {
    if (window.__zhsDiag) return;
    window.__zhsDiag = { paused: [], played: [], seen: 0, total: 0 };
    const record = (kind, fn) => function (...args) {
        try {
            const diag = window.__zhsDiag;
            if (diag.total < 4000) {
                diag.total += 1;
                const stack = String(new Error().stack || '')
                    .split('\n').slice(2, 7).map(s => s.trim()).join(' <- ');
                const entry = {t: Date.now(), stack: stack.slice(0, 600)};
                if (kind === 'pause') diag.paused.push(entry); else diag.played.push(entry);
            }
        } catch (e) {}
        return fn.apply(this, args);
    };
    const proto = window.HTMLMediaElement && window.HTMLMediaElement.prototype;
    if (!proto) return;
    proto.pause = record('pause', proto.pause);
    proto.play = record('play', proto.play);
    document.addEventListener('visibilitychange', () => {
        window.__zhsDiag.events = window.__zhsDiag.events || [];
        window.__zhsDiag.events.push({
            t: Date.now(),
            type: 'visibilitychange',
            hidden: document.hidden,
        });
    }, true);
    window.addEventListener('blur', () => {
        window.__zhsDiag.events = window.__zhsDiag.events || [];
        window.__zhsDiag.events.push({t: Date.now(), type: 'blur'});
    }, true);
})();
"""

SNAPSHOT_JS = r"""
() => {
    const visible = (selector) => {
        try {
            return Array.from(document.querySelectorAll(selector)).filter(el => {
                const s = getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden'
                    && r.width > 0 && r.height > 0;
            }).map(el => (el.className || '').toString().slice(0, 90));
        } catch (e) { return []; }
    };
    const video = document.querySelector('video');
    const diag = window.__zhsDiag || {paused: [], played: [], events: []};
    const now = Date.now();
    const recent = (list) => (list || [])
        .filter(item => now - item.t < 30000)
        .slice(-12)
        .map(item => (item.type ? item.type + (item.hidden === undefined ? '' : ':' + item.hidden)
                                 : item.stack.split(' <- ')[0]));
    return {
        video: video ? {
            paused: video.paused,
            currentTime: Math.round(video.currentTime * 10) / 10,
            duration: Math.round(video.duration * 10) / 10,
            readyState: video.readyState,
            networkState: video.networkState,
            playbackRate: video.playbackRate,
            volume: video.volume,
            muted: video.muted,
            src: (video.currentSrc || '').slice(0, 120),
            error: video.error ? (video.error.code + ':' + video.error.message) : null,
            buffered: (() => {
                try {
                    if (!video.buffered || !video.buffered.length) return 'empty';
                    const parts = [];
                    for (let i = 0; i < video.buffered.length; i++) {
                        parts.push(
                            Math.round(video.buffered.start(i)) + '-'
                            + Math.round(video.buffered.end(i))
                        );
                    }
                    return parts.join(',');
                } catch (e) { return 'err'; }
            })(),
        } : null,
        playerClasses: (() => {
            const el = document.querySelector('#vjs_container, .video-js');
            return el ? el.className : '';
        })(),
        hidden: document.hidden,
        hasFocus: document.hasFocus(),
        pauseCount30s: (diag.paused || []).filter(x => now - x.t < 30000).length,
        playCount30s: (diag.played || []).filter(x => now - x.t < 30000).length,
        recentPauseStacks: recent(diag.paused),
        recentPlayStacks: recent(diag.played),
        recentEvents: recent(diag.events),
        visible: {
            quiz: visible('.ai-test-question-wrapper'),
            notice: visible('.ai-notice-dialog'),
            overlays: visible('.el-overlay'),
            playButton: visible('#playButton, .bigPlayButton'),
            videoArea: visible('.videoArea'),
            topic: visible('.topic-title'),
            error: visible('.vjs-error-display'),
            messages: visible('.el-message, .el-notification, .toast'),
        },
    };
}
"""


def install(page) -> None:
    """给当前页面装上诊断钩子（每次导航都会重新装）。"""
    try:
        page.add_init_script(HOOK_JS)
        page.evaluate(HOOK_JS)
    except Exception:
        pass


def snapshot(page) -> dict | None:
    try:
        return page.evaluate(SNAPSHOT_JS)
    except Exception:
        return None


def dump_page(page, logger: Logger, tag: str = "page") -> str | None:
    """把当前页面的**真实 DOM**导出成文件，方便离线分析页面结构。

    用 page.content() 取的是浏览器序列化后的实时 DOM，
    比手动"复制页面源代码/另存为"可靠得多（后者经常只拿到 head 那一段）。
    """
    import datetime as _dt

    try:
        html = page.content()
    except Exception as exc:
        logger.warn(f"导出页面 HTML 失败：{exc}")
        return None
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = paths.logs_dir() / f"{tag}-{stamp}.html"
    try:
        path.write_text(html, encoding="utf-8")
    except OSError as exc:
        logger.warn(f"写入页面 HTML 失败：{exc}")
        return None
    logger.warn(f"已把当前页面导出到：{path}")
    return str(path)


STRUCTURE_JS = r"""
() => {
    const out = {};
    const add = (key, selector) => {
        try {
            const nodes = Array.from(document.querySelectorAll(selector));
            if (!nodes.length) return;
            out[key] = nodes.slice(0, 4).map(el => ({
                tag: el.tagName,
                cls: (el.className || '').toString().slice(0, 90),
                text: (el.innerText || '').split(/\s+/).join(' ').slice(0, 60),
                kids: el.children.length,
            }));
        } catch (e) {}
    };
    add('试卷容器', '.myschool_ewcon, .examPaper_box');
    add('题目', '.examPaper_subject');
    add('题干', '.subject_describe, .subject_stem');
    add('选项', '.subject_node .nodeLab');
    add('选项正文', '.node_detail');
    add('答题卡', '.answerCard');
    add('按钮', 'button');
    return {
        url: location.href,
        title: document.title,
        bodyText: (document.body ? document.body.innerText : '').slice(0, 300),
        nodes: out,
    };
}
"""


def report_structure(page, logger: Logger, reason: str = "") -> None:
    """把页面的关键结构摘要打进日志（配合 dump_page 使用）。"""
    import json as _json

    try:
        data = page.evaluate(STRUCTURE_JS)
    except Exception as exc:
        logger.warn(f"结构摘要读取失败：{exc}")
        return
    logger.warn(f"===== 页面结构摘要（{reason or '手动'}）=====")
    logger.warn(f"URL: {data.get('url')}")
    logger.warn(f"正文开头: {data.get('bodyText')!r}")
    for key, value in (data.get("nodes") or {}).items():
        logger.warn(f"{key}: {_json.dumps(value, ensure_ascii=False)}")


# 试卷页上一定会出现的字样（出现在题干/选项/按钮里）
EXAM_AX_HINTS = ("提交作业", "暂存作业", "【单选题】", "【多选题】", "【判断题】")


def accessibility_texts(page, limit: int = 60) -> list[str]:
    """用无障碍树取页面可见文字。

    站点会把内容渲染进 closed shadow root（普通 DOM 查不到），
    无障碍树能看到这些内容，所以这里也可以当作"试卷到底有没有打开"的判据。
    """
    try:
        client = page.context.new_cdp_session(page)
        nodes = client.send("Accessibility.getFullAXTree").get("nodes", [])
    except Exception:
        return []
    out: list[str] = []
    for node in nodes:
        if (node.get("role") or {}).get("value") != "StaticText":
            continue
        name = (node.get("name") or {}).get("value")
        if not name:
            continue
        text = " ".join(str(name).split())
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def looks_like_exam_via_accessibility(page) -> bool:
    texts = accessibility_texts(page, limit=80)
    return any(hint in text for text in texts for hint in EXAM_AX_HINTS)


def report(logger: Logger, page, reason: str = "") -> None:
    """把一份诊断快照写进日志（只在需要排查时调用）。"""
    data = snapshot(page)
    if not data:
        logger.warn(f"诊断信息读取失败（{reason}）。")
        return
    lines = [f"===== 播放诊断（{reason or '手动'}） ====="]
    video = data.get("video") or {}
    lines.append(
        "播放器: "
        + ", ".join(f"{key}={value}" for key, value in video.items())
    )
    lines.append(
        f"页面: document.hidden={data.get('hidden')}, "
        f"document.hasFocus={data.get('hasFocus')}"
    )
    lines.append(
        f"最近30秒: pause={data.get('pauseCount30s')} 次, "
        f"play={data.get('playCount30s')} 次"
    )
    lines.append(f"播放器class: {data.get('playerClasses')}")
    lines.append("可见元素: " + json.dumps(data.get("visible"), ensure_ascii=False))
    lines.append(
        "pause 调用来源: " + json.dumps(data.get("recentPauseStacks"), ensure_ascii=False)
    )
    lines.append(
        "play 调用来源: " + json.dumps(data.get("recentPlayStacks"), ensure_ascii=False)
    )
    lines.append("窗口事件: " + json.dumps(data.get("recentEvents"), ensure_ascii=False))
    for line in lines:
        logger.warn(line)
