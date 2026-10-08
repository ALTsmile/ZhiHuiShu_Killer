"""跨 iframe 查找元素的小工具。

智慧树的登录表单、滑块验证在不同版本里可能出现在主框架或 iframe 中，
所以所有"等某个元素出现"的地方都统一走这里。
注意：Playwright 的 bounding_box() 返回的坐标是相对主框架视口的，
因此即使元素在 iframe 里，也可以直接用 page.mouse 操作。
"""

from __future__ import annotations

import time

from playwright.sync_api import Frame, Page


def frames(page: Page):
    """主框架 + 所有子框架，主框架优先。"""
    try:
        return list(page.frames)
    except Exception:
        return [page]


def find_frame(page: Page, selector: str, timeout: float = 0.0) -> Frame | None:
    """返回第一个包含 selector 的框架；timeout 内没找到返回 None。"""
    deadline = time.time() + max(0.0, timeout)
    while True:
        for frame in frames(page):
            try:
                if frame.locator(selector).count() > 0:
                    return frame
            except Exception:
                continue
        if time.time() >= deadline:
            return None
        time.sleep(0.4)


def visible_in_any(page: Page, selectors) -> bool:
    """任意框架里存在可见元素则返回 True。"""
    selector_list = list(selectors)
    for frame in frames(page):
        try:
            if frame.evaluate(
                """(selectors) => selectors.some(selector =>
                    Array.from(document.querySelectorAll(selector)).some(el => {
                        const style = getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        const opacity = Number.parseFloat(style.opacity || '1');
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && opacity > 0.05
                            && rect.width > 0 && rect.height > 0
                            && rect.bottom > 0 && rect.right > 0
                            && rect.top < innerHeight && rect.left < innerWidth;
                    }))""",
                selector_list,
            ):
                return True
        except Exception:
            continue
    return False
