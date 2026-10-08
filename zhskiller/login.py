"""登录流程。

设计要点（踩过坑之后的结论）：

1. **先问账号、后开浏览器**。是否在程序内配置账号密码，在浏览器启动之前就问清楚，
   这样用户的选择不会和浏览器加载页面互相干扰。
2. **手动登录阶段绝不刷新页面**。智慧树对登录页有短时刷新限制：
   二维码刚出来又被刷新，二维码就不会再出现。所以手动登录时只在必要时加载
   一次登录页，之后完全靠「站点登录成功后会自己跳走」来判断，不再 goto。
3. **goto 要容错**。站点经常自己重定向（已登录访问登录页会跳首页），
   Playwright 会抛 "Navigation ... interrupted by another navigation"，
   这不是错误，按正常情况处理。

三种登录方式：
  * 浏览器里已有登录态（独立配置目录上次的登录 / 接管模式下你已登录）——直接跳过；
  * 程序里保存了账号密码——自动填表提交，并处理滑块验证；
  * 否则——手动登录：只在需要时打开一次登录页，然后等用户扫码或输密码。
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from . import captcha, cookies, site
from .bridge import UNANSWERED, Bridge
from .credentials import clear_credentials, load_credentials, save_credentials
from .frames import find_frame
from .logger import Logger

# 手动登录最长等 30 分钟
MANUAL_LOGIN_TIMEOUT = 30 * 60


# ---------------------------------------------------------------------------
# 基础判断
# ---------------------------------------------------------------------------
def on_login_host(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host in site.LOGIN_HOSTS


def is_real_url(url: str) -> bool:
    return urlparse(url or "").scheme in ("http", "https")


def is_login_page(url: str) -> bool:
    """URL 是否还停在登录域。"""
    return on_login_host(url)


def safe_goto(page: Page, url: str, logger: Logger | None = None,
              timeout: float = 60_000, wait_until: str = "commit") -> bool:
    """goto 的容错版本：站点自身跳转导致的 navigation interrupted 不算失败。"""
    try:
        page.goto(url, wait_until=wait_until, timeout=timeout)
        return True
    except Exception as exc:
        message = str(exc)
        first_line = message.splitlines()[0] if message else repr(exc)
        if "interrupted by another navigation" in message or "ERR_ABORTED" in message:
            if logger:
                logger.info("页面被站点自身的跳转打断，继续。")
            return True
        if logger:
            logger.warn(f"打开页面失败：{first_line}")
        return False


def session_ready(session) -> bool:
    """不刷新任何页面的登录状态判断。

    **只看页面 URL 有没有离开登录域**，绝不用 cookie 名字来判断。
    原因（真实踩过的坑）：站点在**未登录**时也会下发 `o_session_id`、
    `JSESSIONID` 这类带 session 字样的 cookie，用 cookie 名做判据会把
    还停在登录页的状态误判成"已登录"，于是程序在用户还没登录时就自作主张
    往下跑（日志表现为"检测到登录成功"之后马上又要求重新登录）。
    """
    page = session.page
    url = page.url
    if not is_real_url(url):
        return False
    # 只要页面上还能**看到**登录表单，就说明没有登录成功。
    # 这条是修一个真实 bug：账号密码输错、或人机验证没通过时，
    # 站点可能把我们丢到一个非登录域名的页面上（看起来"跳走了"），
    # 只看 URL 会误判成登录成功，于是程序开始放视频，实际还停在登录页。
    if has_visible_login_form(page):
        return False
    return not on_login_host(url)


def has_visible_login_form(page: Page) -> bool:
    """页面上是否**可见地**显示着登录表单。

    注意必须判可见：站点登录成功后表单节点常常还留在 DOM 里（只是隐藏了），
    只看"节点存在"会把已登录误判成未登录，程序就会一直等下去。
    """
    frame = _find_login_form(page, timeout=0.1)
    if frame is None:
        return False


def confirm_logged_in(session, logger: Logger, bridge: Bridge, probe_url: str,
                      timeout: float = 75.0) -> bool:
    """最终确认：直接打开课程页，看它会不会把我们踢回登录页。

    这是最可靠的判据。之前的 bug 就是只凭"URL 离开登录域"就认定登录成功，
    结果账号密码输错 / 滑块没过时，站点把我们丢到一个非登录域名的页面上，
    程序以为登录成功就开始放视频，实际还停在登录页。
    """
    if not probe_url:
        return session_ready(session)
    page = session.page
    logger.info("正在确认登录状态（打开课程页验证）…")
    safe_goto(page, probe_url, logger)
    verdict = wait_course_state(page, probe_url, logger, bridge, timeout=timeout)
    if verdict == "ready":
        logger.success("课程页可以正常打开，确认登录成功。")
        _remember(session)
        return True
    if verdict == "login":
        logger.warn("课程页又跳回了登录页，说明还没登录成功。")
    else:
        logger.warn("课程页状态未知，按未登录处理。")
    return False
    try:
        box = frame.locator(site.LOGIN_USERNAME).first
        return box.count() > 0 and box.is_visible()
    except Exception:
        return False


def _remember(session) -> None:
    """登录态有效时顺手把 Cookie 落盘，下次开浏览器直接回注。"""
    now = time.time()
    if now - getattr(session, "_cookie_saved_at", 0.0) < 60:
        return
    try:
        cookies.save(session.context)
    except Exception:
        return
    session._cookie_saved_at = now


def _find_login_form(page: Page, timeout: float = 45.0):
    """登录表单可能嵌在 iframe 里，这里跨框架找。"""
    return find_frame(page, site.LOGIN_USERNAME, timeout=timeout)


def _wait_login_page_ready(session, bridge: Bridge, timeout: float = 60.0) -> str:
    """等登录页就绪。

    返回 "ready"（已经登录，站点把登录页跳走了）/ "form"（登录表单已出现）/
    "timeout"。登录页从 passport 跳到 login 子域要 10~16 秒，所以不能等太短。
    """
    page = session.page
    deadline = time.time() + timeout
    while time.time() < deadline:
        bridge.check_stop()
        if is_real_url(page.url) and not on_login_host(page.url):
            return "ready"
        if _find_login_form(page, timeout=0.1) is not None:
            return "form"
        bridge.sleep(0.5)
    return "timeout"


def wait_course_state(page: Page, course_url: str, logger: Logger, bridge: Bridge,
                      timeout: float = 75.0) -> str:
    """打开课程页后，等它自己稳定下来。

    未登录时课程页会在十几秒后跳到登录页（带 service 参数，登录后会自动跳回来），
    所以这里必须等够时间，不能立刻下结论。

    返回 "login"（跳到了登录页）/ "ready"（课程目录已经渲染出来）/
    "unknown"（超时仍未确定）。
    """
    from . import catalog as catalog_mod  # 局部导入，避免循环依赖

    deadline = time.time() + timeout
    stable_since = time.time()
    last_url = page.url
    while time.time() < deadline:
        bridge.check_stop()
        if on_login_host(page.url):
            return "login"
        if catalog_mod.detect(page, course_url, timeout=2.0) is not None:
            logger.debug("课程目录已渲染，判定为已登录。")
            return "ready"
        if page.url != last_url:
            last_url = page.url
            stable_since = time.time()
        elif time.time() - stable_since >= STABLE_READY_SECONDS:
            logger.debug(f"页面 URL 已稳定 {STABLE_READY_SECONDS} 秒且未跳登录页。")
            return "ready"
        bridge.sleep(0.5)
    return "unknown"


# 页面在课程域上稳定多久就认为「没有被踢到登录页」
STABLE_READY_SECONDS = 25.0


# ---------------------------------------------------------------------------
# 阶段一：打开浏览器之前，先决定登录方式
# ---------------------------------------------------------------------------
def prepare_login(config, logger: Logger, bridge: Bridge) -> dict:
    """决定用自动还是手动登录；需要用户输入账号时，就在这里问完。

    这个函数必须在浏览器启动之前调用。
    """
    if not config.data["login"]["auto_login"]:
        logger.info("未启用自动登录，将使用手动登录。")
        return {"credentials": None, "mode": "manual"}

    saved = load_credentials()
    if saved:
        logger.info("已找到程序内保存的账号，运行时自动登录。")
        return {"credentials": saved, "mode": "auto"}

    if config.data["login"]["prompt_state"] == "muted":
        logger.info("此前选择了「不再提醒」，本次使用手动登录。")
        return {"credentials": None, "mode": "manual"}

    logger.info("程序内还没有保存账号密码。")
    answer = bridge.ask(
        "first_run",
        {
            "message": (
                "还没有在程序里保存智慧树账号密码。\n\n"
                "· 同意：现在输入账号密码，之后由程序自动登录（含滑块验证）\n"
                "· 拒绝：本次手动登录（可用微信扫码），下次运行仍会询问\n"
                "· 不再提醒：以后都手动登录，不再询问"
            )
        },
    )

    if answer == "agree":
        credentials = _ask_credentials_input(logger, bridge)
        if credentials:
            return {"credentials": credentials, "mode": "auto"}
        logger.warn("没有填写账号密码，改为手动登录。")
        return {"credentials": None, "mode": "manual"}

    if answer == "mute":
        config.set("login.prompt_state", "muted")
        config.save()
        logger.info("已记录「不再提醒」，以后登录由你手动完成。")
    elif answer == "decline":
        logger.info("本次选择手动登录，下次运行仍会询问。")
    else:
        logger.info("未做出选择，按手动登录处理。")
    return {"credentials": None, "mode": "manual"}


def _ask_credentials_input(logger: Logger, bridge: Bridge) -> dict | None:
    answer = bridge.ask("credentials", {"message": "请输入智慧树账号与密码："})
    if not isinstance(answer, dict):
        return None
    username = str(answer.get("username") or "").strip()
    password = str(answer.get("password") or "")
    if not username or not password:
        logger.warn("账号或密码为空，跳过保存。")
        return None
    method = str(answer.get("method") or "account")
    school = str(answer.get("school") or "").strip()
    method_name = save_credentials(username, password, method=method, school=school)
    if method_name == "dpapi":
        logger.success("账号密码已用 Windows 凭据保护（DPAPI）加密保存。")
    else:
        logger.success("账号密码已保存（当前系统不支持 DPAPI，仅做混淆存储）。")
    return {"username": username, "password": password,
            "method": method, "school": school}


# ---------------------------------------------------------------------------
# 阶段二：浏览器已打开，执行登录
# ---------------------------------------------------------------------------
def ensure_login(session, config, logger: Logger, bridge: Bridge, plan: dict,
                 probe_url: str = "", probe_timeout: float = 75.0) -> bool:
    page = session.page
    credentials = plan.get("credentials")

    # 统一的登录判定：打开课程页看会落在哪里。
    # 不再看 cookie —— 见 session_ready 的说明。
    if probe_url:
        logger.info("先打开课程页确认登录状态…")
        safe_goto(page, probe_url, logger)
        verdict = wait_course_state(page, probe_url, logger, bridge, timeout=probe_timeout)
        if verdict == "ready":
            logger.success("课程页可以直接打开，判定为已登录。")
            _remember(session)
            return True
        if verdict == "login":
            logger.info("课程页跳转到了登录页，需要登录。")
        elif not on_login_host(page.url):
            # 没识别出目录、但也没被踢到登录页：未登录一定会被重定向，
            # 所以这里按"已登录"继续更稳妥（目录不认识是另一回事）。
            logger.warn("没能确认课程目录，但页面没有跳到登录页，先按已登录继续。")
            return True
        else:
            logger.warn("课程页仍停在加载中，按需要登录处理。")
    elif page.url not in ("", "about:blank") and not on_login_host(page.url):
        logger.success("浏览器里已有登录状态，跳过登录。")
        return True

    if credentials:
        logger.info("开始自动登录…")
        if not on_login_host(page.url):
            safe_goto(page, site.LOGIN_URL, logger)
            state = _wait_login_page_ready(session, bridge, timeout=60)
            if state == "ready":
                logger.success("打开登录页后被站点直接跳转，判定为已登录。")
                return True
            if state != "form":
                logger.warn("登录页加载超时。")
        if auto_login_here(session, config, logger, bridge,
                           credentials["username"], credentials["password"],
                           credentials.get("method"), credentials.get("school")):
            # 自动登录"看起来成功"还不够：必须真的能打开课程页才算数
            # （账号密码错误 / 滑块没过时，站点可能把我们丢到别的页面）
            if confirm_logged_in(session, logger, bridge, probe_url):
                return True
            logger.warn("自动登录后仍然打不开课程页，按未登录处理。")
        logger.warn("自动登录未成功，转为手动登录。")
        return manual_login_here(session, config, logger, bridge)

    # 手动登录：站点通常已经把我们带到登录页了，绝不重复刷新
    if not on_login_host(page.url):
        logger.info("正在打开登录页…")
        safe_goto(page, site.LOGIN_URL, logger)
        _wait_login_page_ready(session, bridge, timeout=60)
    else:
        logger.info("当前就在登录页，请在这里登录（程序不会再刷新页面）。")
    if not manual_login_here(session, config, logger, bridge):
        return False
    # 手动登录同样要做最终确认，避免"看起来登录了其实没有"
    return confirm_logged_in(session, logger, bridge, probe_url)


def _fill_login_form(page: Page, username: str, password: str, logger: Logger,
                     method: str = "account", school: str = "") -> bool:
    try:
        frame = _find_login_form(page, timeout=45.0)
        if frame is None:
            logger.warn("没有找到登录表单（页面可能还在加载或结构已变化）。")
            return False
        if method == "student":
            return _fill_student_login(page, frame, username, password, school, logger)
        username_box = frame.locator(site.LOGIN_USERNAME).first
        if username_box.count() == 0 or not username_box.is_visible():
            # 有些版本默认停在扫码登录，需要先切到账号密码
            for text in ("账号密码登录", "密码登录", "账号登录"):
                tab = frame.get_by_text(text, exact=False).first
                if tab.count() and tab.is_visible():
                    tab.click()
                    page.wait_for_timeout(600)
                    break
        username_box.wait_for(state="visible", timeout=20_000)
        username_box.fill(username)
        password_box = frame.locator(site.LOGIN_PASSWORD).first
        password_box.wait_for(state="visible", timeout=20_000)
        password_box.fill(password)
        agree = frame.locator(site.LOGIN_AGREE).first
        if agree.count():
            try:
                if not agree.is_checked():
                    agree.evaluate("el => el.click()")
            except Exception:
                pass
        page.wait_for_timeout(300)
        submit = frame.locator(site.LOGIN_SUBMIT).first
        submit.wait_for(state="visible", timeout=20_000)
        submit.click()
        return True
    except PlaywrightTimeout:
        logger.warn("等待登录表单超时。")
        return False
    except Exception as exc:
        logger.warn(f"自动填写登录表单失败：{exc}")
        return False


def _fill_student_login(page: Page, frame, username: str, password: str,
                        school: str, logger: Logger) -> bool:
    """学号登录：机构 + 学号 + 密码。

    实测登录页有四个页签：账号登录 / 学号登录 / 工号登录 / 扫码。
    学号登录面板（#pane-2）里的具体控件结构还没拿到样本，
    这里按 Element Plus 的常见结构填写，失败时把面板 HTML 打进日志便于适配。
    """
    logger.info("按「学号登录」方式登录…")
    tab = frame.locator(site.LOGIN_TAB_STUDENT).first
    if tab.count() == 0:
        tab = frame.locator(site.LOGIN_TABS, has_text="学号登录").first
    if tab.count():
        tab.evaluate("el => el.click()")
        page.wait_for_timeout(900)
    else:
        logger.warn("没有找到「学号登录」页签。")
        return False

    pane = frame.locator(site.LOGIN_PANE_STUDENT)
    scope = pane.first if pane.count() else frame
    try:
        if school:
            combo = scope.locator(".el-select input, .el-select").first
            if combo.count():
                combo.click()
                page.wait_for_timeout(400)
                try:
                    combo.fill(school)
                except Exception:
                    page.keyboard.type(school)
                page.wait_for_timeout(900)
                option = scope.locator(
                    ".el-select-dropdown__item, li.el-select-dropdown__item"
                ).first
                if option.count() and option.is_visible():
                    option.click()
                    page.wait_for_timeout(400)
                else:
                    page.keyboard.press("Enter")
            else:
                logger.warn("学号登录面板里没找到机构选择框。")

        boxes = scope.locator("input")
        count = boxes.count()
        filled_password = False
        for index in range(count):
            node = boxes.nth(index)
            try:
                if not node.is_visible():
                    continue
                kind = (node.get_attribute("type") or "text").lower()
                if kind == "password" and not filled_password:
                    node.fill(password)
                    filled_password = True
                elif kind in ("text", "tel") and not node.input_value():
                    node.fill(username)
            except Exception:
                continue
        if not filled_password:
            logger.warn("学号登录面板里没找到密码框。")

        agree = frame.locator(site.LOGIN_AGREE).first
        if agree.count():
            try:
                if not agree.is_checked():
                    agree.evaluate("el => el.click()")
            except Exception:
                pass
        submit = frame.locator(site.LOGIN_SUBMIT).first
        submit.wait_for(state="visible", timeout=20_000)
        submit.click()
        return True
    except Exception as exc:
        logger.warn(f"学号登录填写失败：{exc}")
        try:
            from . import diagnostics

            diagnostics.dump_page(page, logger, "student-login-pane")
        except Exception:
            pass
        return False


def auto_login_here(session, config, logger: Logger, bridge: Bridge,
                    username: str, password: str,
                    method: str | None = None, school: str | None = None) -> bool:
    """在当前登录页上用账号密码登录（不会额外跳转）。"""
    page = session.page
    method = method or str(config.get("login.method", "account"))
    school = school if school is not None else str(config.get("login.school", ""))
    if not _fill_login_form(page, username, password, logger, method, school):
        return False
    logger.info("已提交账号密码，等待登录结果…")
    deadline = time.time() + 90
    captcha_gone_at = None
    resubmits = 0
    while time.time() < deadline:
        bridge.check_stop()
        if captcha.has_verification(page):
            captcha.handle_verification(page, config, logger, bridge)
            captcha_gone_at = time.time()
            continue
        if session_ready(session):
            logger.success("自动登录成功。")
            return True
        # 验证码窗口消失后如果还没登录成功，基本可以断定是验证没过或账号密码有误。
        # 之前这里会一直等到 3 分钟超时，看起来就像"卡住"。
        if captcha_gone_at and time.time() - captcha_gone_at > 8:
            # 滑块连续失败时平台会把验证窗口关掉。这时再点一次「登录」
            # 通常能拿到一张新验证码，比直接放弃（改手动）体验好。
            if resubmits < 1 and has_visible_login_form(page):
                resubmits += 1
                logger.info("验证码窗口已关闭但仍未登录，重新提交一次登录（换一张验证码）…")
                if _fill_login_form(page, username, password, logger, method, school):
                    captcha_gone_at = None
                    continue
            logger.warn(
                "验证码窗口已关闭，但一直没有检测到登录成功"
                "（可能是验证未通过，或账号密码有误）。"
            )
            break
        bridge.sleep(0.8)
    logger.warn("自动登录超时，可能需要手动处理验证码或账号有误。")
    return session_ready(session)


def manual_login_here(session, config, logger: Logger, bridge: Bridge) -> bool:
    """请用户在当前页面完成登录，全程不再刷新页面。

    这里用「投提示 + 轮询」而不是阻塞式询问：
    程序在等待期间自己也会检测登录状态，所以用户登录成功后**不用点任何按钮**
    程序就会自动继续；浮窗也只会出现在屏幕右下角，不会挡住二维码。
    """
    page = session.page
    try:
        # 要用户自己登录了，这时才把浏览器窗口叫到前面（平时不抢焦点）
        page.bring_to_front()
    except Exception:
        pass
    payload = {
        "first": True,
        "message": (
            "请在浏览器里完成智慧树登录（微信扫码或账号密码都行）。\n"
            "登录成功后程序会自动继续，不用点按钮。\n\n"
            "提示：程序不会刷新登录页，二维码不会因为刷新而消失。"
        ),
    }
    request_id = bridge.post("manual_login", payload)
    try:
        deadline = time.time() + MANUAL_LOGIN_TIMEOUT
        while time.time() < deadline:
            bridge.check_stop()
            if captcha.has_verification(page):
                captcha.handle_verification(page, config, logger, bridge)
            if session_ready(session):
                logger.success("检测到登录成功。")
                return True
            answer = bridge.poll(request_id, 1.0)
            if answer is UNANSWERED:
                continue
            if answer is not True:
                logger.warn("用户取消了手动登录。")
                return False
            if session_ready(session):
                logger.success("检测到登录成功。")
                return True
            logger.warn("仍未检测到登录状态，请继续在浏览器里完成登录。")
            bridge.release(request_id)
            request_id = bridge.post("manual_login", dict(payload, first=False))
        logger.warn("等待登录超时，请重新开始。")
        return False
    finally:
        bridge.release(request_id)


def forget_credentials(logger: Logger) -> None:
    clear_credentials()
    logger.info("已删除本地保存的账号密码。")
