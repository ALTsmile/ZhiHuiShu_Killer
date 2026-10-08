"""人机验证（网易易盾滑块）的检测与自动处理。

真实行为：
  * 登录页、课程页都可能随机弹出；
  * 弹出时视频会暂停，页面被遮罩盖住，任何点击都点不到；
  * 所以「自动化卡住」时第一件事就是查这里。

自动识别流程：拉取背景图与拼图块 -> 灰度/Canny 边缘 -> 模板匹配 ->
按图片原始像素到 CSS 像素的比例换算成鼠标位移 -> 带加减速的轨迹拖动。
识别失败时交给用户手动完成，程序暂停等待。
"""

from __future__ import annotations

import math
import random
import time

from playwright.sync_api import Error as PlaywrightError, Page

from . import site
from .bridge import Bridge, Stopped
from .frames import find_frame, visible_in_any
from .logger import Logger


def has_verification(page: Page) -> bool:
    """页面上是否有可见的人机验证。"""
    return visible_in_any(page, site.VERIFY_POPUP_SELECTORS)


def page_closed(page: Page) -> bool:
    """浏览器/标签页是不是已经被关掉了（用户手动关窗口时会出现）。"""
    try:
        return page.is_closed()
    except Exception:
        return True


def _closed_error(exc: Exception) -> bool:
    return "has been closed" in str(exc) or "TargetClosed" in type(exc).__name__


def _safe_has_verification(page: Page) -> bool:
    """浏览器被关掉时不抛异常，当作"没有验证"处理。"""
    try:
        return has_verification(page)
    except PlaywrightError as exc:
        if _closed_error(exc):
            return False
        raise


def verification_passed(page: Page) -> bool:
    """从"验证结果字段"确认是否已经通过。

    易盾会在隐藏输入框 `input[name=NECaptchaValidate]` 里回填验证票据；
    只靠"弹窗消失"判断偶尔会误判，这个字段更可靠。
    """
    for frame in list(page.frames):
        try:
            value = frame.evaluate(
                """() => {
                    const el = document.querySelector(
                        "input[name='NECaptchaValidate'], .yidun_input");
                    return el ? String(el.value || '') : '';
                }"""
            )
        except Exception:
            continue
        if value:
            return True
    return False


def _slider_scope(page: Page):
    """滑块所在的框架（通常是主框架，但登录页可能嵌在 iframe 里）。"""
    return find_frame(page, f"{site.YIDUN_BG}, .yidun_bgimg", timeout=3.0) or page


def wait_until_hidden(page: Page, bridge: Bridge, timeout: float = 6 * 3600) -> float:
    """一直等到验证消失，返回等待秒数。"""
    start = time.time()
    while _safe_has_verification(page):
        if time.time() - start > timeout:
            break
        bridge.sleep(0.5)
    return time.time() - start


def _download(scope, url: str):
    import cv2
    import numpy as np

    # scope 可能是 Page，也可能是 Frame（登录页的人机验证就在 iframe 里）。
    # Frame 没有 .context，要通过 .page.context 拿；这里两种都兼容。
    context = getattr(scope, "context", None)
    if context is None:
        page = getattr(scope, "page", None)
        context = getattr(page, "context", None)
    if context is None:
        raise RuntimeError("无法获取浏览器上下文（下载验证码图片失败）")
    response = context.request.get(url)
    if not response.ok:
        raise RuntimeError(f"验证码图片下载失败：{response.status}")
    array = np.frombuffer(response.body(), np.uint8)
    return cv2.imdecode(array, cv2.IMREAD_COLOR)


def _background_edges(image):
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return cv2.Canny(binary, 500, 900, apertureSize=3)


def _piece_edges(image):
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    inverted = cv2.bitwise_not(gray)
    _, binary = cv2.threshold(inverted, 240, 255, cv2.THRESH_BINARY_INV)
    return cv2.Canny(binary, 500, 900, apertureSize=3)


def _match_offset(scope) -> tuple[float, float]:
    """返回 (拼图块需要移动的距离, 原图到 CSS 的缩放比例)。

    实测页面（易盾滑块）：
        <div class="yidun_bgimg">
          <img class="yidun_bg-img" src="…@2x.jpg">
          <img class="yidun_jigsaw" src="…@2x.png" style="left: 0px;">
        </div>
    两张图都是 @2x，所以匹配结果（原图像素）要乘 bg显示宽/原图宽 换算成 CSS 像素。
    拼图块初始 left 是 0，理论上位移就等于缺口在背景里的 CSS 位置；
    但为了不依赖这个前提，这里直接用「缺口位置 - 拼图块当前位置」算位移。
    """
    import cv2

    # 注意：只能取 img.yidun_bg-img。
    # `.yidun_bgimg` 是包裹用的容器 div，在 DOM 里排在图片前面，
    # 写成 "img.yidun_bg-img, .yidun_bgimg" 会取到容器 —— 容器没有 naturalWidth，
    # 于是报"验证码元素尺寸读取失败"（这就是之前滑块一直失败的原因）。
    bg = scope.locator(site.YIDUN_BG).first
    piece = scope.locator(site.YIDUN_JIGSAW).first
    if bg.count() == 0 or piece.count() == 0:
        raise RuntimeError("未找到验证码背景图或拼图块")

    # 关键：必须等两张图**加载完成**再量尺寸。
    # 之前的日志就是这个坑——图片还没加载就取 naturalWidth，得到 0，
    # 直接抛"验证码元素尺寸读取失败"，7 次尝试全部白跑。
    try:
        scope.wait_for_function(
            """() => {
                const bg = document.querySelector('.yidun_bg-img');
                const piece = document.querySelector('.yidun_jigsaw');
                if (!bg || !piece) return false;
                const loaded = (img) => img.complete && img.naturalWidth > 0
                    && img.getBoundingClientRect().width > 0;
                const visible = (el) => {
                    const s = getComputedStyle(el);
                    return s.display !== 'none' && s.visibility !== 'hidden';
                };
                return loaded(bg) && loaded(piece) && visible(bg) && visible(piece);
            }""",
            timeout=8000,
        )
    except Exception:
        pass

    bg_url = bg.get_attribute("src")
    piece_url = piece.get_attribute("src")
    natural = bg.evaluate("el => ({width: el.naturalWidth, height: el.naturalHeight})")
    bg_box = bg.bounding_box()
    piece_box = piece.bounding_box()
    if not bg_box:
        raise RuntimeError("背景图还没有渲染出尺寸（可能仍在加载）")
    if not piece_box:
        # 拼图块偶尔量不到自己的盒模型，用它的父容器兜底
        # （拼图块初始 left:0 就是相对这个容器的，所以位移算法仍然成立）
        try:
            piece_box = scope.locator(".yidun_bgimg").first.bounding_box()
        except Exception:
            piece_box = None
    if not piece_box:
        raise RuntimeError("验证码元素尺寸读取失败")

    bg_img = _download(scope, bg_url)
    piece_img = _download(scope, piece_url)
    # 图片自身的像素宽通常等价于 naturalWidth；万一页面还没解码出来，
    # 就用下载下来的图片尺寸兜底，彻底不依赖 naturalWidth。
    natural_width = natural.get("width") or bg_img.shape[1]
    if not natural_width:
        raise RuntimeError("无法确定验证码背景图的原始尺寸")
    result = cv2.matchTemplate(
        _background_edges(bg_img), _piece_edges(piece_img), cv2.TM_CCOEFF_NORMED
    )
    _, _, _, max_loc = cv2.minMaxLoc(result)

    scale = bg_box["width"] / float(natural_width)
    target_x = max_loc[0] * scale
    gap_left = bg_box["x"] + target_x
    return {
        "delta": gap_left - piece_box["x"],
        "scale": scale,
        "gap_left": gap_left,
        "piece_left": piece_box["x"],
        "bg_left": bg_box["x"],
        "bg_width": bg_box["width"],
        "natural_width": natural_width,
        "match_x": max_loc[0],
    }


def _ease_offsets(start: float, end: float, steps: int,
                  kind: str = "linear") -> list[float]:
    """生成从 start 到 end 的累计位移序列（相对按下点的水平偏移）。"""
    steps = max(1, int(steps))
    track: list[float] = []
    for index in range(1, steps + 1):
        t = index / steps
        if kind == "accel":          # 起步加速
            t = t * t
        elif kind == "decel":        # 末端减速
            t = 1 - (1 - t) * (1 - t)
        track.append(start + (end - start) * t)
    return track


class _Pointer:
    """鼠标操作的统一入口：优先用 CDP 的 Input 域，失败才退回 page.mouse。

    为什么不用 page.mouse：实测（headless/headful 都一样）同一个页面上的
    **第二次**拖拽，Playwright 的 mouse.move 只会把第一个 mousemove 送到页面，
    后面全部丢失 —— 滑块于是每次都停在缺口前面一截（用户看到的"总是差 1cm"）。
    换成 CDP 直接派发后，连续多次拖拽每次都能完整送达。

    但**按下/松开仍然走 page.mouse**：如果连 mousePressed 也用 CDP 发，
    第二次拖拽同样只会送到第一个 mousemove（实测）。所以分工是：
    按下/松开交给 Playwright，拖动过程走 CDP。
    """

    def __init__(self, page: Page) -> None:
        self.page = page
        self.cdp = None
        self._pressed = False
        try:
            self.cdp = page.context.new_cdp_session(page)
        except Exception:
            self.cdp = None

    def _send(self, **event) -> bool:
        if self.cdp is None:
            return False
        try:
            self.cdp.send("Input.dispatchMouseEvent", event)
            return True
        except Exception:
            self.cdp = None
            return False

    def move(self, x: float, y: float) -> None:
        pressed = self._pressed
        if not self._send(
            type="mouseMoved",
            x=x,
            y=y,
            button="left" if pressed else "none",
            buttons=1 if pressed else 0,
            pointerType="mouse",
        ):
            self.page.mouse.move(x, y)

    def down(self, x: float, y: float) -> None:
        # 先让 Playwright 自己也知道指针在哪，再按下（见类注释）
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self._pressed = True

    def up(self, x: float, y: float) -> None:
        self.page.mouse.move(x, y)      # 把 Playwright 记录的指针挪到松手的位置
        self.page.mouse.up()
        self._pressed = False

    def close(self) -> None:
        if self.cdp is not None:
            try:
                self.cdp.detach()
            except Exception:
                pass
            self.cdp = None


def _drive(pointer: _Pointer, start_x: float, start_y: float,
           offsets: list[float],
           delay: tuple[float, float] = (0.007, 0.019)) -> None:
    """把一串水平偏移走完，顺手加一点纵向抖动，看起来更像手拖。"""
    for offset in offsets:
        pointer.move(
            start_x + offset,
            start_y + random.uniform(-1.2, 1.2),
        )
        time.sleep(random.uniform(*delay))


def _visible_box(scope, selector: str) -> dict | None:
    """取第一个真的渲染出来的元素的盒模型（页面上可能同时存在好几个验证框）。"""
    try:
        locator = scope.locator(selector)
        count = min(locator.count(), 4)
    except Exception:
        return None
    for index in range(count):
        try:
            box = locator.nth(index).bounding_box()
        except Exception:
            continue
        if box and box.get("width") and box.get("height"):
            return box
    return None


def _piece_x(scope) -> float | None:
    """拼图块当前的横坐标（主框架坐标系，拖动过程中也能实时读到）。"""
    box = _visible_box(scope, site.YIDUN_JIGSAW)
    return float(box["x"]) if box else None


def _settled_piece_x(scope, timeout: float = 0.9) -> float | None:
    """等拼图块"停稳"之后的横坐标。

    踩过的坑：易盾给拼图块加了 CSS 过渡，刚拖完立刻量到的是**动到一半**的位置，
    比最终位置滞后十几像素。之前就是照着这个滞后的数字判断"已经对上了"，
    于是每次都停在缺口左边 1~2mm（日志里的"松手前误差 +7.7px"）。
    """
    deadline = time.time() + timeout
    last: float | None = None
    stable_since: float | None = None
    while True:
        now = _piece_x(scope)
        if now is not None:
            if last is not None and abs(now - last) < 0.4:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= 0.15:
                    return now
            else:
                stable_since = None
            last = now
        if time.time() >= deadline:
            return last
        time.sleep(0.04)


# 易盾失败时的提示语（"验证失败，请重新拖动滑块" / "拼图被怪兽吃掉了，换一张试试吧" …）
# 注意别把拖动前的"拖动滑块完成拼图"也算进去，所以只认这些明确的失败词。
_FAILURE_TIP_WORDS = ("失败", "重试", "再试", "换一张", "刷新", "怪兽", "吃掉了", "出错")


def _failure_tip(scope) -> str | None:
    """易盾自己的失败提示；有就说明这一把没通过，可以立刻重试而不是干等。"""
    try:
        tip = scope.locator(".yidun_tips").first
        if tip.count() and tip.is_visible():
            text = (tip.inner_text() or "").strip()
            if text and any(word in text for word in _FAILURE_TIP_WORDS):
                return text
    except Exception:
        pass
    return None


def try_solve_slider(page: Page, logger: Logger, attempts: int = 3,
                     offset: float = 0.0) -> bool:
    """尝试自动拖过滑块，成功返回 True。

    这一版修掉了"每次都差一截/差 1~2mm"的三个成因：

    1. **松手后才量位置**：易盾失败时会把拼图块弹回原点，量到的永远是起点，
       既发现不了误差也没法修正。现在改成按下后**边拖边量**。
    2. **CSS 过渡造成的滞后**：拼图块的 left 有过渡动画，刚拖完读到的位置比
       真实位置滞后十几像素，照着它判断"已经对上了"就会停在缺口左边
       （日志里的"松手前误差"就是这么来的）。现在读位置前会等它停稳。
    3. **鼠标事件丢包**：同一页面上第二次拖拽时 Playwright 的 mouse.move 会
       丢掉绝大部分 mousemove，滑块自然到不了位。现在拖动过程走 CDP 直发。

    具体做法：按下后先小拖一段（探头），读一次拼图块的真实位置，算出
    「鼠标每移动 1px，拼图块实际移动多少」，再按这个比例把剩下的距离补准，
    松手前还会用停稳后的位置反复微调。
    """
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        logger.warn("未安装 opencv-python/numpy，跳过自动滑块验证。")
        return False

    try:
        scope = _slider_scope(page)
    except PlaywrightError as exc:
        if _closed_error(exc):
            logger.warn("浏览器窗口已关闭，停止滑块验证。")
            return False
        raise

    # 每次尝试的目标微调（px）。正常情况下闭环校正后误差在 1~2px 内，
    # 只有"看着对准了平台却不认"时才需要往后挪几个像素。
    bias_plan = (0.0, 6.0, 12.0)
    pointer = _Pointer(page)
    try:
        return _solve_slider_attempts(
            page, scope, logger, pointer, attempts, offset, bias_plan
        )
    finally:
        pointer.close()


def _solve_slider_attempts(page: Page, scope, logger: Logger, pointer: _Pointer,
                           attempts: int, offset: float,
                           bias_plan: tuple[float, ...]) -> bool:
    bias = 0.0
    next_bias = 1
    for index in range(max(1, attempts)):
        if page_closed(page):
            logger.warn("浏览器窗口已关闭，停止滑块验证。")
            return False
        try:
            scope.wait_for_selector(
                ".yidun_bgimg, " + site.YIDUN_BG, state="visible", timeout=4000
            )
            loading = scope.locator(site.YIDUN_LOADING)
            if loading.count() and loading.first.is_visible():
                loading.first.wait_for(state="detached", timeout=8000)
            page.wait_for_timeout(250)      # 等滑块真正可以拖

            info = _match_offset(scope)
            # offset 是用户配置的微调，bias 是程序自己按上一轮结果加的微调；
            # 默认都是 0：正常情况下闭环校正已经把拼图块送到缺口上了。
            gap_left = info["gap_left"] + offset + bias
            target_piece = gap_left

            box = _visible_box(scope, site.YIDUN_SLIDER)
            if not box:
                continue
            start_x = box["x"] + box["width"] / 2
            start_y = box["y"] + box["height"] / 2

            pointer.move(start_x, start_y)
            page.wait_for_timeout(random.randint(80, 180))
            piece_start = _settled_piece_x(scope, timeout=0.4)
            if piece_start is None:
                piece_start = float(info["piece_left"])

            # ---- 抓住滑块（偶尔会按下不生效，先小动一下确认跟手）----
            grabbed = False
            current = 0.0
            for grab_try in range(2):
                pointer.down(start_x, start_y)
                page.wait_for_timeout(random.randint(90, 200))
                _drive(pointer, start_x, start_y,
                       _ease_offsets(0.0, 8.0, 3, "accel"))
                check = _settled_piece_x(scope, timeout=0.6)
                if check is not None and abs(check - piece_start) >= 2.0:
                    grabbed = True
                    current = 8.0
                    break
                if grab_try == 0:
                    logger.info("第一下好像没抓住滑块，松开重按一次。")
                    pointer.up(start_x + 8.0, start_y)
                    page.wait_for_timeout(random.randint(350, 600))
                    piece_start = _settled_piece_x(scope, timeout=0.5) or piece_start
                    pointer.move(start_x, start_y)
                    page.wait_for_timeout(random.randint(80, 160))
            if not grabbed:
                logger.info("拼图块没有跟随鼠标，改用固定补偿继续。")

            # ---- 探头：量出"鼠标动多少，拼图块动多少" ----
            probe = min(max((target_piece - piece_start) * 0.3, 30.0), 60.0)
            _drive(pointer, start_x, start_y,
                   _ease_offsets(current, current + probe, random.randint(6, 9),
                                 "accel"))
            current += probe
            probe_mouse = current
            observed = _settled_piece_x(scope) if grabbed else None
            ratio = 1.0                      # 鼠标 1px -> 拼图块 ratio px
            moved = 0.0
            follows = grabbed
            if observed is not None:
                moved = observed - piece_start
                if moved > 1.0:
                    ratio = moved / current      # current = 到目前一共拖了多少鼠标
                else:
                    follows = False
                    logger.info("探头阶段拼图块没怎么动，改用固定补偿继续。")
            ratio = min(max(ratio, 0.2), 5.0)
            # 读不到/不跟随鼠标时退回"按几何算 + 固定补偿"的老办法
            if not follows:
                target_piece = gap_left + (offset or 36.0)
            # 拼图块还差多少 -> 鼠标还要挪多少
            if observed is None:
                need = current + (target_piece - (piece_start + current)) / ratio
            else:
                need = current + (target_piece - observed) / ratio
            # 兜底：别因为比例量歪了去拖一个荒谬的距离
            travel_cap = info["bg_width"] * 1.6 + 60
            need = min(max(need, current), travel_cap)

            # ---- 第二段：中段快、末端慢地滑到目标 ----
            cruise = current + (need - current) * 0.82
            _drive(pointer, start_x, start_y,
                   _ease_offsets(current, cruise, random.randint(14, 22)))
            _drive(pointer, start_x, start_y,
                   _ease_offsets(cruise, need, random.randint(8, 14), "decel"))
            current = need

            # 用"整段累计位移"再校准一次比例：比短探头准（探头那几十毫秒里
            # 拼图块往往还没追上鼠标）。
            if follows:
                settled = _settled_piece_x(scope)
                if settled is not None and current > 1:
                    cumulative = (settled - piece_start) / current
                    if 0.2 <= cumulative <= 5:
                        ratio = cumulative

            # ---- 第三段：松手前小幅微调（真人也会这样对一下） ----
            residual = None
            for _ in range(3 if follows else 0):
                now = _settled_piece_x(scope)
                if now is None:
                    break
                residual = target_piece - now
                if abs(residual) <= 2.0:
                    break
                destination = min(current + residual / ratio, current + 120)
                _drive(pointer, start_x, start_y,
                       _ease_offsets(current, destination, random.randint(3, 6),
                                     "decel"),
                       delay=(0.012, 0.03))
                current = destination
            if follows:                 # 松手前再确认一次，日志里的误差才是真的
                check = _settled_piece_x(scope)
                if check is not None:
                    residual = target_piece - check
            final_error = "" if residual is None else f"，松手前误差 {residual:+.1f}px"

            ratio_text = (
                f"实测比例 {ratio:.2f}" if follows else "无法实测比例（按 1:1 + 固定补偿）"
            )
            bias_text = f"目标加 {bias:.0f}px 微调，" if bias else ""
            logger.info(
                f"第 {index + 1} 次尝试自动完成滑块验证：背景 {info['bg_width']:.0f}px 显示 /"
                f" {info['natural_width']}px 原图（缩放 {info['scale']:.3f}），"
                f"{bias_text}缺口 {gap_left:.1f}px，{ratio_text}"
                f"（前 {probe_mouse:.0f}px 鼠标 → 拼图块 {moved:+.1f}px），"
                f"鼠标共拖 {current:.1f}px{final_error}"
            )

            # 真人松手前会停顿一下
            page.wait_for_timeout(random.randint(100, 260))
            pointer.up(start_x + current, start_y)

            # 成功判据必须是**整个验证弹窗消失**，不能只看背景图。
            # 踩过的坑：易盾在"换题/校验中"也会把 .yidun_bgimg 藏一下，
            # 旧代码据此报"滑块验证已通过"，其实登录根本没通过，
            # 于是程序以为登录成功、开始放视频，人却还卡在登录页。
            solved = False
            gone_since: float | None = None
            failed_tip: str | None = None
            deadline = time.time() + 6.0
            while time.time() < deadline:
                if _safe_has_verification(page):
                    gone_since = None
                    failed_tip = _failure_tip(scope)
                    if failed_tip:
                        break
                else:
                    if gone_since is None:
                        gone_since = time.time()
                    elif time.time() - gone_since > 1.0:
                        solved = True
                        break
                page.wait_for_timeout(300)

            if solved:
                if verification_passed(page):
                    logger.success("滑块验证已通过（票据已回填）。")
                else:
                    logger.info("验证弹窗已消失（是否真的通过由后续步骤验证）。")
                return True

            # 位置明明对上了却被判失败 -> 说明"该停哪"本身差了一截，
            # 下一轮把目标整体往后挪（这正是"每次都差 1cm"的场景）。
            if (follows and residual is not None and abs(residual) <= 6
                    and next_bias < len(bias_plan)):
                bias = bias_plan[next_bias]
                next_bias += 1
                logger.info(f"滑块位置已经对上了，下次尝试把目标再往后挪 {bias:.0f}px。")

            if failed_tip:
                logger.info(f"易盾提示：{failed_tip}")
            logger.warn("滑块验证未通过，重试中…")
            page.wait_for_timeout(1200)
        except Stopped:
            raise
        except PlaywrightError as exc:
            if _closed_error(exc):
                logger.warn("浏览器窗口已关闭，停止滑块验证。")
                return False
            logger.warn(f"自动滑块处理异常：{exc}")
            try:
                page.wait_for_timeout(1000)
            except PlaywrightError:
                return False
        except Exception as exc:
            logger.warn(f"自动滑块处理异常：{exc}")
            try:
                page.wait_for_timeout(1000)
            except PlaywrightError:
                return False
    return False


def handle_verification(page: Page, config, logger: Logger, bridge: Bridge) -> float:
    """处理一次人机验证，返回被占用的秒数。

    先尝试自动；自动失败或未开启时，通过界面弹窗请用户手动完成并等待。
    """
    if page_closed(page):
        return 0.0
    if not _safe_has_verification(page):
        return 0.0
    start = time.time()
    logger.warn("检测到人机验证（滑块），视频已暂停。")

    solved = False
    if config.data["captcha"]["auto_slider"]:
        solved = try_solve_slider(
            page, logger,
            offset=float(config.get("captcha.slider_offset", 0) or 0),
        )

    if not solved and _safe_has_verification(page):
        if config.data["captcha"]["notify_user"]:
            logger.warn("需要手动完成人机验证，请在浏览器中操作。")
            try:
                # 只有"需要你动手"的时候才把浏览器窗口叫到前面，平时不抢焦点
                page.bring_to_front()
            except Exception:
                pass
            request_id = bridge.post(
                "captcha",
                {"message": "请在浏览器里完成滑块验证，完成后程序会自动继续。"},
            )
        else:
            request_id = None
        try:
            wait_until_hidden(page, bridge)
        finally:
            if request_id is not None:
                bridge.release(request_id)

    if _safe_has_verification(page):
        logger.warn("人机验证仍未通过，本轮将暂停该课程。")
    else:
        # 窗口消失≠一定通过：连续失败几次后平台也会自己把窗口关掉。
        # 所以这里只说"窗口已消失"，是否真的通过由调用方（登录/播放）去验证。
        logger.info("人机验证窗口已消失（是否通过由后续步骤验证）。")
    return time.time() - start
