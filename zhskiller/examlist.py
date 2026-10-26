"""「作业考试」列表页：直接看未提交的作业 / 测试，比逐个点课程目录快得多。

实测结构（bbb.html）：
    <div id="examStateTab">
      <li id="examStateTabWsj">未提交</li><li id="examStateTabYsj">已提交</li>
    </div>
    <div id="examBox"><ul><li><div class="examItemWrap">
        <div class="examTit"><a><span class="name">第八章单元测试</span></a></div>
        <ul class="examInfoList">…<li>对应章节 <span>第八章</span></li>…</ul>
        <a class="jobExamComBtn" title="开始答题">开始答题</a>
    </div></li></ul></div>
"""

from __future__ import annotations

import time

from playwright.sync_api import Page

from .bridge import Bridge
from .logger import Logger

PENDING_TAB = "#examStateTabWsj"
SUBMITTED_TAB = "#examStateTabYsj"
ITEM = "#examBox .examItemWrap, .examBox .examItemWrap"
START = "a.jobExamComBtn, .jobExamComBtn"


def is_list_page(page: Page) -> bool:
    """是不是「作业考试」列表页。"""
    try:
        return bool(
            page.evaluate(
                """() => Boolean(
                    document.querySelector('#examBox, #examStateTabWsj')
                    || /作业考试/.test(document.body ? document.body.innerText : '')
                )"""
            )
        )
    except Exception:
        return False


def _text_at(page: Page, text: str, index: int = 0):
    try:
        items = page.get_by_text(text, exact=True)
        if index < items.count():
            return items.nth(index)
    except Exception:
        pass
    return None


def _click_visible(page: Page, text: str) -> bool:
    """点页面上可见的某段文字（连同它的可点父级一起冒泡）。"""
    try:
        items = page.get_by_text(text, exact=True)
        count = min(items.count(), 6)
    except Exception:
        return False
    for index in range(count):
        item = items.nth(index)
        try:
            if not item.is_visible():
                continue
            box = item.bounding_box()
            if not box or box["width"] < 2 or box["height"] < 2:
                continue
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            return True
        except Exception:
            continue
    return False


def _click_entry(course_page: Page, logger: Logger) -> bool:
    """在课程页找到并点开「作业考试」入口。"""
    if _click_visible(course_page, "作业考试"):
        return True
    # wisdom-mooc 那一版的入口收在「菜单」里，先把菜单打开
    if _click_visible(course_page, "菜单"):
        course_page.wait_for_timeout(700)
        if _click_visible(course_page, "作业考试"):
            return True
    # 兜底：按容器文字找
    for selector in (".item:has-text('作业考试')", ".router-item:has-text('作业考试')",
                     "a:has-text('作业考试')", "li:has-text('作业考试')"):
        try:
            locator = course_page.locator(selector).first
            if locator.count() and locator.is_visible():
                box = locator.bounding_box()
                if box and box["width"] > 2 and box["height"] > 2:
                    course_page.mouse.click(
                        box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                    )
                    return True
        except Exception:
            continue
    logger.debug("没找到「作业考试」入口。")
    return False


def open_list(course_page: Page, logger: Logger, bridge: Bridge,
              timeout: float = 25.0) -> Page | None:
    """从课程页打开「作业考试」列表；打不开返回 None（调用方回退到目录扫描）。"""
    if is_list_page(course_page):
        return course_page
    try:
        before = len(course_page.context.pages)
    except Exception:
        before = 1
    if not _click_entry(course_page, logger):
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        bridge.check_stop()
        try:
            pages = course_page.context.pages
            for candidate in pages[before:]:
                if is_list_page(candidate):
                    try:
                        candidate.wait_for_load_state("domcontentloaded", timeout=8000)
                    except Exception:
                        pass
                    logger.info("已打开「作业考试」列表。")
                    return candidate
            if is_list_page(course_page):
                return course_page
        except Exception:
            pass
        bridge.sleep(0.7)
    logger.warn("点开了「作业考试」入口，但没等到列表页。")
    return None


READ_JS = r"""
() => {
    const out = [];
    const wraps = Array.from(document.querySelectorAll(
        '#examBox .examItemWrap, .examBox .examItemWrap'));
    wraps.forEach((el, index) => {
        const titleNode = el.querySelector('.examTit .name, .examTit');
        const title = titleNode
            ? (titleNode.innerText || '').replace(/\s+/g, ' ').trim() : '';
        let chapter = '';
        for (const li of el.querySelectorAll('.examInfoList li')) {
            const text = (li.innerText || '').replace(/\s+/g, ' ').trim();
            const hit = text.match(/对应章节\s*[:：]?\s*(.+)$/);
            if (hit) { chapter = hit[1].trim(); break; }
        }
        const start = el.querySelector("a.jobExamComBtn, .jobExamComBtn");
        if (!title || !start) return;
        const style = getComputedStyle(start);
        const rect = start.getBoundingClientRect();
        if (style.display === 'none' || style.visibility === 'hidden'
            || rect.width < 2 || rect.height < 2) return;
        start.setAttribute('data-zhs-start', String(index));
        out.push({index: index, title: title, chapter: chapter});
    });
    return out;
}
"""


def read_pending(page: Page, logger: Logger) -> list[dict]:
    """切到「未提交」标签，读出还没提交的作业 / 测试。"""
    try:
        tab = page.locator(PENDING_TAB).first
        if tab.count():
            classes = tab.get_attribute("class") or ""
            if "cur" not in classes:
                box = tab.bounding_box()
                if box:
                    page.mouse.click(box["x"] + box["width"] / 2,
                                     box["y"] + box["height"] / 2)
                    page.wait_for_timeout(800)
    except Exception:
        pass
    try:
        return list(page.evaluate(READ_JS) or [])
    except Exception as exc:
        logger.debug(f"读取作业考试列表失败：{exc}")
        return []


def _is_exam_url(url: str) -> bool:
    text = (url or "").lower()
    return "dohomework" in text or "stuexamweb" in text or "/exam" in text


def start_item(page: Page, item: dict, logger: Logger, bridge: Bridge,
               timeout: float = 25.0) -> Page | None:
    """点某个条目的「开始答题」，返回打开的答题页（新标签页或当前页）。"""
    try:
        before = len(page.context.pages)
    except Exception:
        before = 1
    try:
        button = page.locator(f"[data-zhs-start='{item['index']}']").first
        box = button.bounding_box() if button.count() else None
        if not box:
            return None
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    except Exception as exc:
        logger.debug(f"点「开始答题」失败：{exc}")
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        bridge.check_stop()
        try:
            pages = page.context.pages
            for candidate in pages[before:]:
                if _is_exam_url(candidate.url):
                    try:
                        candidate.wait_for_load_state("domcontentloaded", timeout=8000)
                    except Exception:
                        pass
                    return candidate
            if _is_exam_url(page.url):
                return page
        except Exception:
            pass
        bridge.sleep(0.7)
    return None


def refresh(page: Page, logger: Logger, bridge: Bridge) -> None:
    """刷新列表页（提交完一条后未提交列表会变）。"""
    try:
        page.reload(wait_until="domcontentloaded", timeout=30000)
    except Exception:
        try:
            page.goto(page.url, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            logger.debug(f"刷新作业考试列表失败：{exc}")
    bridge.sleep(1.2)
