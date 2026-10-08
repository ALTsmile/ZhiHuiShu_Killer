"""浏览器的启动与接管。

两种模式：
  new    程序自己开一个浏览器（使用 data/browser_profile 独立配置目录，
         登录状态天然持久化，也不会和用户日常使用的浏览器抢锁）；
  attach 接管用户已经打开的浏览器（需要对方带 --remote-debugging-port 启动）。
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

from . import cookies as cookies_mod
from . import paths, site
from .logger import Logger

LAUNCH_ARGS = [
    "--start-maximized",
    "--disable-blink-features=AutomationControlled",
    # 浏览器层面静音：页面里的 video.volume 保持站点设置的值不被我们改动，
    # 但整个浏览器不出声。这样既安静，又不会因为"音量=0"影响平台计进度。
    "--mute-audio",
    "--disable-infobars",
    "--no-default-browser-check",
    "--no-first-run",
    # 窗口被别的程序挡住 / 最小化时，Chrome 会认定"被遮挡"进而把渲染节流，
    # 合成出来的鼠标事件、视频帧都会变得不可靠。挂机刷课恰恰是"人不在电脑前"
    # 的场景，所以这里明确关掉这些节流。
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    "--disable-features=CalculateNativeWinOcclusion,Translate,OptimizationHints",
]


@dataclass
class BrowserSession:
    playwright: Playwright
    browser: Browser | None
    context: BrowserContext
    page: Page
    attached: bool

    def new_page(self) -> Page:
        page = self.context.new_page()
        apply_stealth(page)
        return page

    def close(self) -> None:
        """只关闭本程序拥有/创建的页面，不动用户原有的浏览器。"""
        try:
            if self.attached:
                if not self.page.is_closed():
                    self.page.close()
            else:
                self.context.close()
        except Exception:
            pass
        try:
            self.playwright.stop()
        except Exception:
            pass


def apply_stealth(page: Page) -> None:
    """抹掉最明显的自动化特征。

    做不到「完全不可检测」，但可以避免被最基础的 navigator.webdriver 检测拒之门外。
    另外无头模式下 UA 会带 HeadlessChrome，智慧树登录页会因此一直卡在 Loading，
    所以这里顺手用 CDP 覆盖掉。
    """
    script = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5].map(i => ({name: 'Plugin ' + i})),
    });
    window.chrome = window.chrome || {runtime: {}};
    const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
    if (originalQuery) {
        window.navigator.permissions.query = (parameters) => (
            parameters && parameters.name === 'notifications'
                ? Promise.resolve({state: Notification.permission})
                : originalQuery(parameters)
        );
    }
    """
    try:
        page.add_init_script(script)
        page.evaluate(script)
    except Exception:
        pass
    try:
        user_agent = page.evaluate("() => navigator.userAgent")
        if "HeadlessChrome" in (user_agent or ""):
            client = page.context.new_cdp_session(page)
            client.send(
                "Network.setUserAgentOverride",
                {"userAgent": user_agent.replace("HeadlessChrome", "Chrome")},
            )
    except Exception:
        pass


SAME_TAB_JS = r"""
(() => {
    if (window.__zhsSameTabPatched) return;
    window.__zhsSameTabPatched = true;
    const originalOpen = window.open;
    // 有些站点会 window.open('about:blank') 或不带参数拿到窗口句柄，
    // 之后再 w.location.href = 真正地址。这种情况不能直接返回 null
    // （会让站点代码抛错，考试就永远打不开），
    // 这里返回一个"代理窗口"：给它赋 location 就等于导航当前标签页。
    const makeStub = () => {
        const stub = {
            closed: false,
            opener: window,
            focus() {}, blur() {}, close() {},
            postMessage() {},
        };
        Object.defineProperty(stub, 'location', {
            get() { return window.location; },
            set(value) {
                if (!value) return;
                if (typeof value === 'string') { window.location.href = value; return; }
                if (value.href) { window.location.href = value.href; }
            },
        });
        return stub;
    };
    window.open = function (url, name, features) {
        try {
            if (!url) return makeStub();
            if (typeof url === 'string' && !/^https?:/i.test(url)) {
                // about:blank、javascript: 之类：交给代理，后续赋值会落到当前页
                return makeStub();
            }
            if (typeof url === 'string' && url.indexOf('zhihuishu.com') >= 0) {
                // 章节测验/考试默认开新标签页，而新标签页可能拿不到登录态
                // （站点把凭证放在 sessionStorage 里，sessionStorage 不跨标签页共享），
                // 结果就是新标签页被踢到登录页。改成当前标签页打开，登录态天然保留。
                window.location.href = url;
                return makeStub();
            }
            return originalOpen.call(window, url, name, features);
        } catch (e) {
            return makeStub();
        }
    };
})();
"""


def install_same_tab_redirect(page: Page) -> None:
    """让站点在"新标签页打开"的页面改成当前标签页打开，保住登录态。"""
    try:
        page.add_init_script(SAME_TAB_JS)
        page.evaluate(SAME_TAB_JS)
    except Exception:
        pass


def _apply_reset_login(context: BrowserContext, logger: Logger) -> None:
    """菜单里点过「重置登录」的话，这里把浏览器里的登录凭证清掉。"""
    flag = paths.reset_login_flag()
    if not flag.is_file():
        return
    try:
        context.clear_cookies()
        cookies_mod.clear()
        logger.warn("已按「重置登录」的要求清空登录状态，本次需要重新登录。")
    except Exception as exc:
        logger.warn(f"清空登录状态失败：{exc}")
    finally:
        try:
            flag.unlink()
        except OSError:
            pass


def _launch_options(config) -> dict:
    browser_cfg = config.data["browser"]
    driver = browser_cfg["driver"]
    exe_path = browser_cfg.get("exe_path") or paths.which_browser(driver)
    options: dict = {
        "headless": False,
        "args": list(LAUNCH_ARGS),
        "viewport": None,
    }
    if exe_path:
        options["executable_path"] = exe_path
    else:
        options["channel"] = "msedge" if driver == "edge" else "chrome"
    return options


def open_session(config, logger: Logger) -> BrowserSession:
    """按配置创建浏览器会话。"""
    browser_cfg = config.data["browser"]
    playwright = sync_playwright().start()
    try:
        if browser_cfg["mode"] == "attach":
            return _attach(playwright, config, logger)
        return _launch(playwright, config, logger)
    except Exception:
        playwright.stop()
        raise


def _launch(playwright: Playwright, config, logger: Logger) -> BrowserSession:
    browser_cfg = config.data["browser"]
    user_data_dir = str(paths.profile_dir())
    options = _launch_options(config)
    logger.info(f"正在启动 {browser_cfg['driver']} 浏览器（独立配置目录）…")
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            context = playwright.chromium.launch_persistent_context(user_data_dir, **options)
            browser = context.browser
            page = context.pages[0] if context.pages else context.new_page()
            apply_stealth(page)
            page.set_default_timeout(30_000)
            _apply_reset_login(context, logger)
            restored = cookies_mod.restore(context)
            if restored:
                logger.info(f"已回注 {restored} 条上次保存的登录 Cookie。")
            logger.success("浏览器已启动。")
            return BrowserSession(playwright, browser, context, page, attached=False)
        except Exception as exc:  # 首次启动偶发失败，重试一次
            last_error = exc
            logger.warn(f"浏览器启动失败（第 {attempt + 1} 次）：{exc}")
            time.sleep(1.5)
    raise RuntimeError(f"浏览器启动失败：{last_error}")


def _attach(playwright: Playwright, config, logger: Logger) -> BrowserSession:
    cdp_url = config.data["browser"]["cdp_url"]
    logger.info(f"正在连接已打开的浏览器：{cdp_url}")
    try:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
    except Exception as exc:
        raise RuntimeError(
            f"连接 {cdp_url} 失败。请先用带调试端口的浏览器打开智慧树，"
            "或改用「新开浏览器」模式。"
        ) from exc
    if not browser.contexts:
        raise RuntimeError("已连接浏览器，但没有可用的窗口。")
    context = browser.contexts[0]
    page = _pick_zhihuishu_page(context) or context.new_page()
    apply_stealth(page)
    page.set_default_timeout(30_000)
    logger.success("已接管已打开的浏览器窗口，将复用它现有的登录状态。")
    return BrowserSession(playwright, browser, context, page, attached=True)


def _pick_zhihuishu_page(context: BrowserContext) -> Page | None:
    for page in context.pages:
        url = page.url or ""
        if "zhihuishu.com" in url and "login" not in url:
            return page
    for page in context.pages:
        if "zhihuishu.com" in (page.url or ""):
            return page
    return None


def launch_debuggable_browser(driver: str = "chrome", port: int = 9222) -> str:
    """给用户开一个带远程调试端口的浏览器，返回可执行文件路径。

    Chrome 136 之后，使用默认配置目录时 --remote-debugging-port 会被忽略，
    所以这里固定使用一个独立目录。
    """
    exe = paths.which_browser(driver)
    if not exe:
        raise RuntimeError(f"没有找到 {driver} 的可执行文件。")
    profile = paths.data_dir() / f"debug_profile_{driver}"
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "https://www.zhihuishu.com",
        ],
        close_fds=True,
    )
    return exe
