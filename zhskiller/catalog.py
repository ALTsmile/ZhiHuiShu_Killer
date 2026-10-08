"""课程目录识别与课时枚举。"""

from __future__ import annotations

import time
from urllib.parse import urlparse

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeout

from . import site
from .bridge import Bridge
from .logger import Logger


def detect(page: Page, url: str = "", timeout: float = 30) -> site.Catalog | None:
    """识别当前页面属于哪种课程布局。"""
    candidates = site.catalog_candidates(url or page.url)
    combined = ", ".join(catalog.item for catalog in candidates)
    try:
        page.wait_for_selector(combined, state="attached", timeout=int(timeout * 1000))
    except PlaywrightTimeout:
        return None
    except Exception:
        # 页面正在跳转时可能抛出 Execution context was destroyed 之类的瞬态错误，
        # 这里当成"还没找到"处理，由外层轮询重试。
        return None
    for catalog in candidates:
        try:
            if page.locator(catalog.item).count() > 0:
                return catalog
        except Exception:
            continue
    return None


def detect_with_recovery(page: Page, url: str, config, logger: Logger, bridge: Bridge,
                         timeout: float = 60) -> site.Catalog | None:
    """识别目录；期间如果弹出人机验证就先处理掉。

    如果页面被站点踢回登录页，直接返回 None（由上层去处理登录），
    免得白等到超时再报"识别不到目录"。
    """
    from . import captcha  # 局部导入避免循环依赖

    deadline = time.time() + timeout
    while time.time() < deadline:
        host = urlparse(page.url).hostname or ""
        if host.lower() in site.LOGIN_HOSTS:
            return None
        if captcha.has_verification(page):
            captcha.handle_verification(page, config, logger, bridge)
            deadline = time.time() + timeout
            continue
        remaining = max(1.0, deadline - time.time())
        found = detect(page, url, timeout=min(3.0, remaining))
        if found:
            return found
        bridge.sleep(0.5)
    return None


def expand_folds(page: Page, logger: Logger) -> int:
    """展开折叠的章节（融合课默认折叠，不展开会漏课时）。"""
    try:
        count = page.evaluate(
            """(args) => {
                const {item, header} = args;
                let n = 0;
                document.querySelectorAll(item).forEach(el => {
                    if (!el.className.includes('is-active')) {
                        const h = el.querySelector(header);
                        if (h) { h.click(); n++; }
                    }
                });
                return n;
            }""",
            {"item": site.COLLAPSE_ITEM, "header": site.COLLAPSE_HEADER},
        )
        if count:
            page.wait_for_timeout(500)
            logger.debug(f"已展开 {count} 个折叠章节。")
        return count or 0
    except Exception:
        return 0


def list_lessons(page: Page, catalog: site.Catalog) -> list[Locator]:
    try:
        page.wait_for_selector(catalog.item, timeout=5000)
    except PlaywrightTimeout:
        pass
    return page.locator(catalog.item).all()


def parse_progress(value) -> int:
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(int(number), 100))


def lesson_progress(lesson: Locator, catalog: site.Catalog) -> int:
    """读取平台记录的课时进度百分比。

    注意顺序：**先读进度环，再看完成标记**。
    新版共享课的完成勾（.child-check）在进度 >= 80% 时就会出现，
    如果先看勾就会把只学了 80% 的课时误判成 100% 而跳过。
    """
    value = _read_progress_node(lesson, catalog)
    if value is not None:
        return value
    try:
        if catalog.finish and lesson.locator(catalog.finish).count() > 0:
            return 100
    except Exception:
        pass
    return 0


def _read_progress_node(lesson: Locator, catalog: site.Catalog) -> int | None:
    """读进度节点；页面上没有这个节点时返回 None（区别于"读到 0%"）。"""
    if not catalog.progress:
        return None
    try:
        node = lesson.locator(catalog.progress).first
        if node.count() == 0:
            return None
        if catalog.progress_attr:
            raw = node.get_attribute(catalog.progress_attr)
            if raw is None:
                return None
            return parse_progress(raw)
        text = node.text_content()
        if text is None:
            return None
        return parse_progress(text)
    except Exception:
        return None


def is_complete(lesson: Locator, catalog: site.Catalog) -> bool:
    return lesson_progress(lesson, catalog) >= 100


def unfinished(lessons: list[Locator], catalog: site.Catalog) -> list[Locator]:
    return [lesson for lesson in lessons if not is_complete(lesson, catalog)]


def lesson_title(page: Page, lesson: Locator, catalog: site.Catalog) -> str:
    try:
        node = lesson.locator(catalog.title).first
        if node.count() == 0:
            node = page.locator(catalog.title).first
        if node.count():
            title = node.get_attribute("title")
            if title:
                return " ".join(title.split())
            text = node.text_content()
            if text:
                return " ".join(text.split())
    except Exception:
        pass
    try:
        return " ".join((lesson.text_content() or "当前课时").split())[:60]
    except Exception:
        return "当前课时"


def wait_active(lesson: Locator, catalog: site.Catalog, timeout: float = 8) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            classes = (lesson.get_attribute("class") or "").split()
            if catalog.active_class in classes:
                return True
            if lesson.locator(catalog.active).count() > 0:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def course_title(page: Page, catalog: site.Catalog) -> str:
    if not catalog.course_title:
        return ""
    try:
        node = page.locator(catalog.course_title).first
        if node.count():
            return " ".join((node.text_content() or "").split())
    except Exception:
        pass
    return ""


_CATALOG_HIDDEN_JS = """
(selector) => {
    const box = document.querySelector(selector);
    if (!box) return 'no-box';
    // hide-box 可能加在容器自己身上（随堂练习弹出），也可能加在外层
    // <aside class="el-aside a-side hide-box"> 上（用户手动收起侧边栏），
    // 所以要顺着父节点往上找一圈。
    let node = box;
    for (let i = 0; i < 5 && node && node !== document.body; i++) {
        const classes = String(node.className || '').split(/\\s+/);
        if (classes.indexOf('hide-box') >= 0) return 'hide-box';
        node = node.parentElement;
    }
    return '';
}
"""


def catalog_hidden_reason(page: Page) -> str:
    """目录被收起时返回原因（'' 表示看起来正常）。"""
    try:
        return str(page.evaluate(_CATALOG_HIDDEN_JS, site.CATALOG_BOX) or "")
    except Exception:
        return ""


def expand_catalog(page: Page, logger: Logger) -> bool:
    """点目录的展开按钮（`.side-expand-box` / `.collapse-btn`）。"""
    for selector in site.CATALOG_EXPAND_BUTTON:
        try:
            button = page.locator(selector).first
            if not button.count() or not button.is_visible():
                continue
            button.evaluate(
                """(el) => el.dispatchEvent(new MouseEvent('click', {
                    bubbles: true, cancelable: true, view: window,
                }))"""
            )
            return True
        except Exception:
            continue
    return False


def ensure_visible(page: Page, catalog: site.Catalog, logger: Logger) -> bool:
    """确保章节目录是展开可见的。

    实测：随堂练习弹出时站点会把目录收起（`.wisdom-category-box` 加 `hide-box`），
    用户手动点收起侧边栏时 `hide-box` 则是加在**外层 `<aside class="el-aside
    a-side hide-box">`** 上（日志里"目录明明在、条目就是点不动"就是这个原因）。
    所以这里要往上层找，不能只看容器自己的 class。
    """
    acted = False
    for attempt in range(3):
        reason = catalog_hidden_reason(page)
        if reason == "no-box":
            # 连容器都找不到时才退回"看目录项可不可见"。
            # （不要用容器的 bounding box 判断：空节点尺寸为 0，会被误判成不可见，
            #   于是本来没折叠也去点展开按钮 —— 反而把展开的目录点收起来了。）
            try:
                items = page.locator(catalog.item)
                count = items.count()
                if count and not any(
                    items.nth(index).is_visible() for index in range(min(count, 5))
                ):
                    reason = "items-hidden"
                else:
                    reason = ""
            except Exception:
                reason = ""

        if not reason:
            if acted:
                logger.info("章节列表已重新展开。")
            return True

        if not acted:
            logger.warn(
                "章节列表被折叠/隐藏了（通常是随堂练习弹出或手动收起侧边栏导致），"
                "正在展开…"
            )
            acted = True
        if not expand_catalog(page, logger):
            logger.warn("没有找到章节列表的展开按钮。")
        page.wait_for_timeout(700)
    logger.warn("章节列表仍然不可见，可能被弹窗挡住了。")
    return False


def click_lesson(page: Page, lesson: Locator, logger: Logger) -> bool:
    """点击课时项，尽量稳。

    目录是个可滚动容器，直接 Playwright click 经常报
    "element is outside of the viewport"（实测会连等 10 秒再重试，
    表现就是切课时卡顿）。这里先把元素在各级滚动容器里滚到中间，
    再用真实鼠标点；实在不行就退回直接派发 click 事件。
    """
    try:
        lesson.evaluate(
            """(el) => {
                let node = el.parentElement;
                while (node && node !== document.body) {
                    const style = getComputedStyle(node);
                    const scrollable = /(auto|scroll|overlay)/.test(
                        style.overflowY + ' ' + style.overflowX);
                    if (scrollable && node.scrollHeight > node.clientHeight + 4) {
                        const top = el.getBoundingClientRect().top
                            - node.getBoundingClientRect().top;
                        node.scrollTop += top - node.clientHeight / 2 + el.clientHeight / 2;
                    }
                    node = node.parentElement;
                }
                el.scrollIntoView({block: 'center', inline: 'nearest'});
            }"""
        )
    except Exception:
        pass
    page.wait_for_timeout(300)

    try:
        box = lesson.bounding_box()
        if box and box.get("width") and box.get("height"):
            page.mouse.click(
                box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            )
            return True
    except Exception as exc:
        logger.debug(f"鼠标点击课时失败：{exc}")

    try:
        lesson.evaluate(
            """(el) => el.dispatchEvent(new MouseEvent('click', {
                bubbles: true, cancelable: true, view: window,
            }))"""
        )
        return True
    except Exception as exc:
        logger.debug(f"派发点击课时失败：{exc}")
        return False


# ---------------------------------------------------------------------------
# 章节测验条目
# ---------------------------------------------------------------------------
def chapter_item_selector(page: Page) -> str | None:
    """返回目录里实际能匹配到章节测验的选择器。"""
    for selector in site.CHAPTER_ITEM_SELECTORS:
        try:
            if page.locator(selector).count() > 0:
                return selector
        except Exception:
            continue
    return None


def chapter_item_title(item: Locator) -> str:
    for selector in site.CHAPTER_ITEM_TITLE_SELECTORS:
        try:
            node = item.locator(selector).first
            if node.count():
                text = " ".join((node.text_content() or "").split())
                if text:
                    return text[:60]
        except Exception:
            continue
    # 兜底：整块文字里把按钮文案（"去完成"之类）去掉
    try:
        text = " ".join((item.text_content() or "").split())
    except Exception:
        text = ""
    for noise in site.CHAPTER_ITEM_TITLE_NOISE:
        text = text.replace(noise, "")
    text = " ".join(text.split())
    return text[:60] or "章节测验"


def chapter_item_info(item: Locator) -> dict:
    """尽量判断章节测验是否已完成。

    新版的完成标记还没实测到，所以这里把条目的类名/图标类名一并返回，
    先打印到日志里，方便以后按真实结构收紧判断。
    """
    info = {"title": chapter_item_title(item), "done": False, "marks": []}
    # 章节名：这门课里所有章节测验的名字都叫「测试」，
    # 需要靠所属章节才能区分，否则没法稳定地记录"这个做过了"。
    try:
        info["chapter"] = " ".join(
            (
                item.evaluate(
                    """(el) => {
                        const pick = (root) => {
                            if (!root || !root.querySelector) return '';
                            const node = root.querySelector('.category-title, .item-name, .course-nav-header');
                            return node ? (node.innerText || '').trim() : '';
                        };
                        let node = el;
                        for (let i = 0; i < 6 && node; i++) {
                            const parent = node.parentElement;
                            if (!parent) break;
                            const own = pick(parent);
                            if (own) return own;
                            let sib = node.previousElementSibling;
                            while (sib) {
                                const text = pick(sib);
                                if (text) return text;
                                sib = sib.previousElementSibling;
                            }
                            node = parent;
                            if (node === document.body) break;
                        }
                        return '';
                    }"""
                )
                or ""
            ).split()
        )[:40]
    except Exception:
        info["chapter"] = ""
    try:
        info["classes"] = " ".join((item.get_attribute("class") or "").split())
    except Exception:
        info["classes"] = ""
    try:
        html = item.evaluate("el => el.outerHTML").lower()
    except Exception:
        html = ""
    try:
        info["html"] = " ".join(item.evaluate("el => el.outerHTML").split())[:400]
    except Exception:
        info["html"] = ""
    for hint in site.CHAPTER_ITEM_DONE_HINTS:
        if hint.lower() in html:
            info["marks"].append(hint)
    # 目录里的完成勾（和课时共用一套标记）
    try:
        if item.locator(site.WISDOM.finish).count() > 0:
            info["marks"].append(site.WISDOM.finish)
    except Exception:
        pass
    info["done"] = bool(info["marks"])
    # 目录条目的状态文字：实测是 <span class="float-right">去完成</span>，
    # 已完成时显示别的字样（"已完成"/"100%"之类），据此判断最可靠。
    try:
        status_node = item.locator(".float-right").first
        info["status"] = (
            " ".join((status_node.text_content() or "").split())
            if status_node.count()
            else ""
        )
    except Exception:
        info["status"] = ""
    if info["status"]:
        text = info["status"]
        if "去完成" not in text and "开始" not in text and "未" not in text:
            info["done"] = True
            info["marks"].append(f"状态:{text}")
        else:
            info["done"] = False
    return info


def list_chapter_items(page: Page, logger: Logger) -> list[Locator]:
    selector = chapter_item_selector(page)
    if not selector:
        return []
    items = page.locator(selector).all()
    logger.info(f"目录里找到 {len(items)} 个章节测验条目（选择器 {selector}）。")
    for index, item in enumerate(items, 1):
        info = chapter_item_info(item)
        logger.info(
            f"  [{index}] {info['title']}"
            f" | 判定={'已完成' if info['done'] else '未完成'}"
            f" | class={info['classes'][:60]!r}"
            f" | 命中标记={info['marks']}"
        )
    return items
