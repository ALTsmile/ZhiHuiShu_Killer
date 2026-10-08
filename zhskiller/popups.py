"""各类弹窗：网络错误、公告、学前必读、以及「是否被挡住」的通用判断。"""

from __future__ import annotations

import time

from playwright.sync_api import Page

from . import site
from .bridge import Bridge
from .logger import Logger

# 判定为「网络错误」类提示框的关键字
ERROR_HINTS = ("网络", "错误", "失败", "异常", "连接超时", "重新连接", "加载失败", "断开")
# 判定为「练习题」类的关键字，这类弹窗不在这里处理
QUIZ_HINTS = ("随堂练习", "章节练习", "章节测试", "题目", "答题", "测验", "课后作业")

_JS_VISIBLE = """
(selector) => Array.from(document.querySelectorAll(selector)).some(el => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const opacity = Number.parseFloat(style.opacity || '1');
    return style.display !== 'none'
        && style.visibility !== 'hidden'
        && opacity > 0.05
        && rect.width > 0 && rect.height > 0
        && rect.bottom > 0 && rect.right > 0
        && rect.top < innerHeight && rect.left < innerWidth;
})
"""


def is_visible(page: Page, selector: str) -> bool:
    try:
        return bool(page.evaluate(_JS_VISIBLE, selector))
    except Exception:
        return False


def any_visible(page: Page, selectors) -> bool:
    return any(is_visible(page, selector) for selector in selectors)


def is_video_blocked(page: Page) -> bool:
    """视频被遮罩挡住（随堂练习、公告弹窗、人机验证、模态框）时为真。

    这个判断很重要：弹窗挡着的时候站点会主动暂停视频，
    如果程序还一直去 play()，就会变成"播放-暂停"来回抖，
    而且进度也不会增长。
    """
    from . import captcha  # 局部导入，避免与 captcha 形成循环依赖

    return (
        any_visible(page, site.BLOCKING_OVERLAYS)
        or any_visible(page, site.MODAL_OVERLAY_SELECTORS)
        or captcha.has_verification(page)
    )


def _inspect_dialogs(page: Page) -> list[dict]:
    """列出当前可见的对话框及其内容摘要。"""
    try:
        return page.evaluate(
            """(roots) => {
                const out = [];
                for (const selector of roots) {
                    for (const el of document.querySelectorAll(selector)) {
                        const style = getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        if (rect.width < 40 || rect.height < 30) continue;
                        const text = (el.innerText || '').slice(0, 600);
                        const buttons = Array.from(
                            el.querySelectorAll('button, .el-button, a.layui-layer-btn0, .dialog-btn')
                        ).map(b => (b.innerText || '').trim()).filter(Boolean);
                        out.push({text, buttons, index: out.length});
                    }
                }
                return out;
            }""",
            list(site.DIALOG_ROOTS),
        )
    except Exception:
        return []


def click_button_in_dialog(page: Page, texts, logger: Logger, skip_quiz: bool = True) -> str | None:
    """在最上层可见对话框里点击指定文字的按钮，返回被点的对话框文本。"""
    try:
        clicked_text = page.evaluate(
            """(args) => {
                const {roots, texts, skipQuiz, quizHints} = args;
                const visible = [];
                for (const selector of roots) {
                    for (const el of document.querySelectorAll(selector)) {
                        const style = getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        if (rect.width < 40 || rect.height < 30) continue;
                        visible.push(el);
                    }
                }
                // 后出现的更靠上层
                for (let i = visible.length - 1; i >= 0; i--) {
                    const el = visible[i];
                    const content = el.innerText || '';
                    if (skipQuiz && quizHints.some(h => content.includes(h))) continue;
                    const buttons = Array.from(
                        el.querySelectorAll('button, .el-button, a.layui-layer-btn0, .dialog-btn')
                    );
                    for (const want of texts) {
                        const hit = buttons.find(b => (b.innerText || '').trim().includes(want));
                        if (hit) {
                            hit.click();
                            return content.slice(0, 200);
                        }
                    }
                }
                return null;
            }""",
            {
                "roots": list(site.DIALOG_ROOTS),
                "texts": list(texts),
                "skipQuiz": skip_quiz,
                "quizHints": list(QUIZ_HINTS),
            },
        )
        return clicked_text
    except Exception as exc:
        logger.debug(f"点击弹窗按钮失败：{exc}")
        return None


def has_error_dialog(page: Page) -> bool:
    """页面上是否正弹着「网络异常/加载失败」这类必须点确定的提示框。"""
    for dialog in reversed(_inspect_dialogs(page)):
        text = dialog.get("text") or ""
        if any(hint in text for hint in QUIZ_HINTS):
            continue
        if any(hint in text for hint in ERROR_HINTS):
            return True
    return False


def dismiss_error_dialog(page: Page, logger: Logger) -> bool:
    """处理「网络错误」之类必须确认的提示框。"""
    if not has_error_dialog(page):
        return False
    content = click_button_in_dialog(page, site.DIALOG_CONFIRM_TEXTS, logger)
    if content:
        logger.warn(f"检测到异常提示框，已点击确定：{_shorten(content)}")
        return True
    return False


def dismiss_any_dialog(page: Page, logger: Logger) -> bool:
    """兜底：关掉任何不含题目的对话框（防止页面被卡住）。"""
    content = click_button_in_dialog(page, site.DIALOG_CONFIRM_TEXTS, logger)
    if content:
        logger.info(f"已关闭弹窗：{_shorten(content)}")
        return True
    return False


def close_announcements(page: Page, logger: Logger) -> bool:
    """关闭学前必读 / 学习时长 / 公告之类的提示。"""
    acted = False
    acted |= close_ai_notice(page, logger)
    try:
        close_icon = page.locator(site.PREREAD_CLOSE).first
        if close_icon.count() and close_icon.is_visible():
            close_icon.click(timeout=2000)
            logger.info("已关闭学前必读弹窗。")
            acted = True
    except Exception:
        pass

    for selector in (site.STUDYTIME_DIV, site.POPUP_CLOSE_ICON):
        try:
            if is_visible(page, selector):
                page.evaluate(
                    "(sel) => { const el = document.querySelector(sel); if (el) el.click(); }",
                    selector,
                )
                logger.info("已关闭页面提示弹窗。")
                acted = True
        except Exception:
            pass

    # 夜间模式的「休息提醒」按钮，点掉即可
    try:
        if is_visible(page, site.PATTERN_BTN):
            page.evaluate(
                "(sel) => { const el = document.querySelector(sel); if (el) el.click(); }",
                site.PATTERN_BTN,
            )
            acted = True
    except Exception:
        pass
    return acted


def close_ai_notice(page: Page, logger: Logger) -> bool:
    """新版共享课的「AI 助教提示」弹窗：只能点右侧按钮关闭。

    编译后的模板里这个弹窗设置了 show-close=false / close-on-click-modal=false，
    按 Esc 和点遮罩都关不掉，所以必须点到那个按钮，否则视频后面的弹题也出不来。
    """
    try:
        button = page.locator(site.AI_NOTICE_CLOSE).first
        if button.count() and button.is_visible():
            button.click(timeout=2000)
            logger.info("已关闭 AI 助教提示弹窗。")
            return True
    except Exception:
        pass
    return False


def wait_for_no_overlay(page: Page, bridge: Bridge, logger: Logger, timeout: float = 600) -> bool:
    """等待随堂练习等遮罩消失（用户选择手动作答时用）。"""
    start = time.time()
    warned = False
    while is_video_blocked(page):
        if time.time() - start > timeout:
            return False
        if not warned:
            logger.warn("页面被弹窗遮住，等待人工处理…")
            warned = True
        bridge.sleep(0.5)
    return True


def _shorten(text: str, limit: int = 60) -> str:
    flat = " ".join((text or "").split())
    return flat[:limit] + ("…" if len(flat) > limit else "")
