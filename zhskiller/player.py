"""单个课时的播放与监控。

核心循环（每 0.5 秒一轮）：
  人机验证 -> 题目弹窗 -> 网络错误弹窗 -> 平台进度 -> 播放器状态 -> 保活/恢复
完成判据用「平台记录的进度」而不是播放器时间，
因为智慧树经常出现「视频没播完但进度已经记满」和「播完了进度还差一点」两种情况。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import random

from playwright.sync_api import Locator, Page

from . import captcha, catalog as catalog_mod, diagnostics, popups, quiz, site
from .ai import AiClient
from .bridge import Bridge
from .logger import Logger


# 仅章节测验模式下用的小脚本：让页面里的视频始终保持暂停。
# 站点会在各种时机自己续播（切课时、弹窗关掉后、页面刷新后…），
# 所以既要拦 play 事件，也要定时兜底扫一遍。
KEEP_PAUSED_SOURCE = """
(() => {
    if (window.__zhsKeepPaused) return;
    window.__zhsKeepPaused = true;
    const stop = (video) => {
        try { if (!video.paused) { video.pause(); } } catch (e) {}
    };
    const scan = () => {
        try {
            document.querySelectorAll('video').forEach(stop);
        } catch (e) {}
    };
    scan();
    document.addEventListener('play', (event) => {
        const node = event.target;
        if (node && node.tagName === 'VIDEO') stop(node);
    }, true);
    window.__zhsPauseTimer = setInterval(scan, 800);
})();
"""

RELEASE_PAUSED_SOURCE = """
(() => {
    window.__zhsKeepPaused = false;
    if (window.__zhsPauseTimer) {
        clearInterval(window.__zhsPauseTimer);
        window.__zhsPauseTimer = null;
    }
})();
"""


def install_keep_video_paused(page: Page) -> None:
    """仅章节测验模式专用：让视频一直暂停，不弹随堂练习。

    视频在后台播着的话，过一会儿就会弹随堂练习；那个面板会盖住整个页面、
    还会把章节列表收起来，程序点目录条目就"点了没反应"，直接卡死。

    故意只用 evaluate（不用 add_init_script）：接管模式下 init script 没法撤销，
    下次换成"刷视频"模式时会把视频永远按住。现在改成在仅答题流程里反复
    调用（每次处理一条测验前都调一次），页面跳转后自然会重新装上。

    注意要**每个 frame 都装**：这门课的视频播放器在 iframe 里，
    只扫主框架的话视频照样会偷偷播起来。
    """
    frames = [page]
    try:
        frames = list(page.frames) or [page]
    except Exception:
        pass
    for frame in frames:
        try:
            frame.evaluate(KEEP_PAUSED_SOURCE)
        except Exception:
            continue


def release_keep_video_paused(page: Page) -> None:
    """撤掉"保持暂停"看门狗（切回刷视频模式时用）。"""
    frames = [page]
    try:
        frames = list(page.frames) or [page]
    except Exception:
        pass
    for frame in frames:
        try:
            frame.evaluate(RELEASE_PAUSED_SOURCE)
        except Exception:
            continue

POLL_INTERVAL = 0.5
# 1.5 倍速下视频播完时平台往往还没记满（平台按观看时长计时），
# 所以需要回退重播若干次，上限放宽一些。
SEEK_BACK_LIMIT = 8
# 回退重播一轮后进度几乎没动就放弃，避免无意义地空转
SEEK_BACK_MIN_GAIN = 2
# 视频在播但平台进度多久没涨就补一次鼠标操作
PROGRESS_STALL_SECONDS = 90
PROGRESS_PROBE_INTERVAL = 60


def human_activity(page: Page, logger: Logger, rounds: int = 3,
                   allow_click: bool = True) -> None:
    """模拟真人的鼠标操作。

    智慧树有"防挂机"逻辑：页面长时间没有任何真实输入事件时，
    学习时长可能**根本不上报**（日志里表现为视频在播、平台进度一直是 0%）。
    Playwright 的 mouse.move/click 走的是真实输入通道（trusted event），
    所以这里用小幅鼠移动 + 来回微滚动来"唤醒"上报。

    特意避开视频区域中心：那里是播放/暂停切换键的命中位置。
    需要真实 click 时，只点顶部栏的空白处，并且点完立刻校验 URL 有没有被改掉。
    """
    try:
        size = page.evaluate("() => ({w: innerWidth, h: innerHeight})") or {}
        width = int(size.get("w") or 1280)
        height = int(size.get("h") or 720)
    except Exception:
        width, height = 1280, 720
    try:
        for _ in range(max(1, rounds)):
            x = random.randint(int(width * 0.60), int(width * 0.94))
            y = random.randint(int(height * 0.10), int(height * 0.30))
            page.mouse.move(x, y, steps=random.randint(10, 20))
            page.wait_for_timeout(random.randint(80, 200))
            # 来回各滚一点，净位移为 0，不会真的把页面滚走
            page.mouse.wheel(0, 120)
            page.wait_for_timeout(random.randint(60, 160))
            page.mouse.wheel(0, -120)
            page.wait_for_timeout(random.randint(120, 260))
        if allow_click:
            _safe_click(page, logger, width)
    except Exception as exc:
        logger.debug(f"模拟鼠标操作失败：{exc}")


def _safe_click(page: Page, logger: Logger, width: int) -> None:
    """在顶部栏空白处点一下（真实 click）。

    有些站点的"学习时长上报"要等到收到真实 click 才开始，
    所以这里补一次；点完立刻确认没把自己点跑。
    """
    before_url = page.url
    try:
        x = int(width * 0.5) + random.randint(-40, 40)
        y = random.randint(28, 46)
        page.mouse.click(x, y)
        page.wait_for_timeout(180)
        if page.url != before_url:
            logger.warn("模拟点击意外触发了跳转，正在返回课程页…")
            page.go_back()
            page.wait_for_timeout(1500)
    except Exception as exc:
        logger.debug(f"模拟点击失败：{exc}")


@dataclass
class LessonResult:
    completed: bool
    paused_seconds: float = 0.0
    reached_limit: bool = False
    reason: str = ""
    quizzes: int = 0


# ---------------------------------------------------------------------------
# 播放器原子操作
# ---------------------------------------------------------------------------
def video_state(page: Page) -> dict | None:
    try:
        return page.evaluate(
            """() => {
                const v = document.querySelector('video');
                if (!v) return null;
                return {
                    paused: v.paused,
                    ended: v.ended,
                    currentTime: v.currentTime,
                    duration: v.duration,
                    volume: v.volume,
                    muted: v.muted,
                    playbackRate: v.playbackRate,
                    readyState: v.readyState,
                };
            }"""
        )
    except Exception:
        return None


def wait_for_video(page: Page, bridge: Bridge, timeout: float = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = video_state(page)
        if state and isinstance(state.get("duration"), (int, float)) and state["duration"] > 0:
            return True
        bridge.sleep(0.5)
    state = video_state(page)
    return bool(state)


def tune(page: Page, config, logger: Logger, announce: bool = False) -> None:
    """调节播放器。

    音量策略（按用户要求改过）：
      * mute_browser=True（默认）：**不碰 video.volume**，改由浏览器层面静音
        （启动参数 --mute-audio）。这样页面看到的是站点自己的音量值，
        "音量=0 导致平台不计进度"的风险就完全避开了。
      * mute_browser=False：退回老做法，把 video.volume 设成配置值（别设 0）。
    """
    playback = config.data["playback"]
    mute_in_browser = bool(playback.get("mute_browser", True))
    volume = playback["volume"]
    speed = config.data["playback"]["speed"]
    try:
        result = page.evaluate(
            """(args) => {
                const {volume, speed, muteInBrowser} = args;
                const v = document.querySelector('video');
                if (!v) return null;
                const changes = [];
                if (!muteInBrowser && Math.abs(v.volume - volume) > 0.01) {
                    v.volume = volume;
                    changes.push('音量->' + volume);
                }
                // 元素级静音会让平台看到 muted=true，一律取消掉
                if (v.muted) {
                    v.muted = false;
                    changes.push('取消静音');
                }
                if (Math.abs(v.playbackRate - speed) > 0.01) {
                    v.playbackRate = speed;
                    changes.push('倍速->' + speed);
                }
                // 同步播放器界面上的显示
                const label = document.querySelector('.speedBox span');
                if (label) label.innerText = 'X ' + speed;
                const box = document.querySelector('.volumeBox');
                if (box) box.classList.remove('volumeNone');
                return changes;
            }""",
            {"volume": volume, "speed": speed, "muteInBrowser": mute_in_browser},
        )
        if result and announce:
            logger.info(f"播放器已设置：{'，'.join(result)}")
    except Exception as exc:
        logger.debug(f"调节播放器失败：{exc}")


def click_play_button(page: Page, logger: Logger) -> bool:
    """点播放器自己那个"大播放按钮"。

    注意只点 video.js 的 poster 播放键：站点的 #playButton 是**播放/暂停切换**键，
    点它有可能反而把正在播放的视频按停（这就是之前"一播一停"的元凶之一）。
    """
    try:
        button = page.locator(".vjs-big-play-button").first
        if button.count() and button.is_visible():
            button.click(timeout=1500)
            logger.debug("已点击播放器的大播放按钮。")
            return True
    except Exception as exc:
        logger.debug(f"点击大播放按钮失败：{exc}")
    return False


def force_play(page: Page, logger: Logger, gesture: bool = False) -> bool:
    if gesture:
        click_play_button(page, logger)
    try:
        return bool(
            page.evaluate(
                """() => {
                    const v = document.querySelector('video');
                    if (!v) return false;
                    const p = v.play();
                    if (p && p.catch) p.catch(() => {});
                    return true;
                }"""
            )
        )
    except Exception as exc:
        logger.debug(f"恢复播放失败：{exc}")
        return False


def ensure_play_started(page: Page, logger: Logger) -> bool:
    """开课时如果视频是暂停的，用**真实鼠标**点一次播放按钮。

    为什么单独做这一步：正常浏览网站时，人是先点播放再开始看的，
    这一次真实点击会带来用户激活（user activation）。
    程序直接调 video.play() 没有这个"手势"，站点可能因此不计学习时长
    （日志里表现为视频在播、平台进度长时间停在 0%）。
    只在**暂停时**点，避免把正在播放的视频点停。
    """
    state = video_state(page)
    if not state or not state.get("paused"):
        return False
    for selector in (".vjs-big-play-button", ".vjs-play-control"):
        try:
            node = page.locator(selector).first
            if not node.count() or not node.is_visible():
                continue
            box = node.bounding_box()
            if not box:
                continue
            page.mouse.click(
                box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            )
            logger.info("视频处于暂停，已用真实鼠标点击播放按钮（模拟真人操作）。")
            page.wait_for_timeout(800)
            return True
        except Exception as exc:
            logger.debug(f"点击播放按钮失败({selector})：{exc}")
    return force_play(page, logger)


def seek(page: Page, seconds: float) -> None:
    try:
        page.evaluate(
            """(time) => {
                const v = document.querySelector('video');
                if (!v) return;
                v.currentTime = Math.max(0, Math.min(time, v.duration - 0.5));
                v.play().catch(() => {});
            }""",
            max(0.0, seconds),
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 弹窗与验证的统一处理
# ---------------------------------------------------------------------------
def handle_interruptions(page: Page, config, logger: Logger, bridge: Bridge,
                         ai: AiClient | None, deep: bool = True) -> float:
    """处理人机验证、随堂练习、网络错误弹窗。返回被打断的秒数。

    deep=False 时跳过较重的题目扫查，用于高频轮询省开销。
    """
    paused = 0.0
    quizzes = 0

    if captcha.has_verification(page):
        paused += captcha.handle_verification(page, config, logger, bridge)

    # 先清掉公告/AI 助教提示这类"不是题目但会挡住页面"的弹窗，
    # 否则它们的文案里带"随堂练习"几个字，容易被误当成题目。
    if deep:
        popups.close_announcements(page, logger)

    if popups.is_visible(page, site.TOPIC_TITLE) or (deep and quiz.has_quiz(page)):
        started = time.time()
        if quiz.handle_quiz(page, config, logger, bridge, ai, "in_video", "随堂练习"):
            quizzes += 1
        paused += time.time() - started
        # 弹窗关掉后视频通常还是暂停的
        tune(page, config, logger)
        if not popups.is_video_blocked(page):
            force_play(page, logger)

    if popups.dismiss_error_dialog(page, logger):
        paused += 2.0
        bridge.sleep(1.0)
    return paused, quizzes


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------
def watch_lesson(page: Page, lesson: Locator, catalog: site.Catalog, config,
                 logger: Logger, bridge: Bridge, ai: AiClient | None,
                 course_start: float, paused_before: float,
                 netlog=None) -> LessonResult:
    """播放并监控当前课时，直到平台进度记满、达到时限或确认卡死。"""
    playback = config.data["playback"]
    speed = playback["speed"]
    limit_minutes = playback["max_minutes_per_course"]
    stall_limit = playback["stall_seconds"]

    if not wait_for_video(page, bridge, timeout=60):
        logger.warn("没有找到可播放的视频元素。")
        return LessonResult(False, reason="未找到视频")

    if config.get("diagnostics.record_events", True):
        diagnostics.install(page)
    # 刚开课时先来一轮真人鼠标操作：智慧树没有输入事件时可能不上报学习时长
    simulate = bool(playback.get("simulate_activity", True))
    if simulate:
        logger.info("模拟一次鼠标操作，尝试触发展开学习时长上报…")
        human_activity(page, logger, rounds=3)
    # 视频如果是暂停状态，用真实鼠标点一下播放按钮（模仿真人进入课程）
    ensure_play_started(page, logger)
    before = video_state(page) or {}
    logger.info(
        f"播放器初始状态：音量 {before.get('volume')}，静音 {before.get('muted')}，"
        f"倍速 {before.get('playbackRate')}"
    )
    tune(page, config, logger, announce=True)
    after = video_state(page) or {}
    logger.info(
        f"设置后：音量 {after.get('volume')}，静音 {after.get('muted')}，"
        f"倍速 {after.get('playbackRate')}"
        f"（目标 音量 {playback['volume']} / 倍速 {speed}）"
    )
    force_play(page, logger)

    paused_seconds = 0.0
    last_catalog_progress = -1
    last_video_time = -1.0
    last_activity = time.monotonic()
    seek_backs = 0
    end_progress_mark = 0
    recovery_count = 0
    iteration = 0
    blocked_rounds = 0
    resume_times: list[float] = []
    cooldown_until = 0.0
    last_resume_at = 0.0
    resume_attempts = 0
    next_diag_at = 0.0
    stalled_but_counting = False
    progress_changed_at = time.monotonic()
    last_probe_at = 0.0
    quiz_count = 0
    last_status_minute = -1

    while True:
        bridge.check_stop()
        loop_started = time.time()
        iteration += 1

        interrupt_pause, interrupt_quizzes = handle_interruptions(
            page, config, logger, bridge, ai, deep=(iteration % 4 == 1)
        )
        paused_seconds += interrupt_pause
        quiz_count += interrupt_quizzes
        # 站点偶尔会把倍速/音量改回去，每轮都对齐一次（内部只在有变化时才动手）
        tune(page, config, logger)

        # ---- 达到单门课程时限 ----
        if limit_minutes > 0:
            elapsed = (time.time() - course_start - paused_before - paused_seconds) / 60
            if elapsed >= limit_minutes:
                logger.warn(f"已学习 {elapsed:.1f} 分钟，达到本门课程时限。")
                return LessonResult(
                    False, paused_seconds, reached_limit=True, reason="达到时限",
                    quizzes=quiz_count,
                )

        # ---- 平台进度 ----
        progress = catalog_mod.lesson_progress(lesson, catalog)
        if progress >= 100:
            logger.success(f"平台已记录该课时 100%（播放器 {last_video_time:.0f}s）。")
            return LessonResult(True, paused_seconds, quizzes=quiz_count)
        if progress > last_catalog_progress:
            last_catalog_progress = progress
            last_activity = time.monotonic()
            recovery_count = 0
            progress_changed_at = time.monotonic()

        state = video_state(page)
        if state is None:
            bridge.sleep(POLL_INTERVAL)
            continue

        current = state.get("currentTime") or 0.0
        duration = state.get("duration") or 0.0
        if current > last_video_time + 0.25:
            last_video_time = current
            last_activity = time.monotonic()
            recovery_count = 0

        # ---- 播放器暂停时尝试恢复 ----
        # 但页面被弹窗/验证遮住时绝不抢播放：站点会立刻再暂停一次，
        # 结果就是"播放-暂停"来回抖，而且学习进度不会增长。
        blocked = popups.is_video_blocked(page)
        if blocked:
            blocked_rounds += 1
            # 注意：这里只"不抢播放"，绝不主动 pause。
            # 主动暂停会和服务端/页面自己的播放状态打架，反而制造出抖动。
            # 遮罩一直在、但又没有可处理的弹窗：可能只是常驻图层，
            # 不能因为这个就一直不恢复播放。
            if blocked_rounds == 8:
                popups.dismiss_any_dialog(page, logger)
            elif blocked_rounds > 20:
                logger.debug("遮罩层长时间存在且无弹窗可处理，忽略遮罩继续尝试播放。")
                blocked = False
                blocked_rounds = 0
            elif iteration % 20 == 0:
                logger.info("页面被弹窗遮住，暂不抢播放，等弹窗处理完。")
        else:
            blocked_rounds = 0
        if not blocked and state.get("paused") and not state.get("ended"):
            now = time.monotonic()
            # 恢复播放要克制：最多每 2 秒一次，而且不点 #playButton
            # （那是播放/暂停切换键，点它反而会把正在播的视频按停）。
            if now >= cooldown_until and now - last_resume_at >= 2.0:
                last_resume_at = now
                resume_attempts += 1
                tune(page, config, logger)
                # 试了 5 次还没起来，才去点 video.js 自己的大播放按钮（只会在暂停时出现）
                force_play(page, logger, gesture=(resume_attempts % 5 == 0))
                resume_times.append(now)
                resume_times = [t for t in resume_times if now - t < 12.0]
            # 反复"恢复→立刻又被暂停"说明站点在主动按停，这时抓一份诊断，
            # 看看到底是谁调的 pause（不再瞎猜）。
            recent = [t for t in resume_times if now - t < 8.0]
            if len(recent) >= 4 and now >= next_diag_at:
                next_diag_at = now + 120
                logger.warn(
                    f"视频被反复暂停又恢复（{len(recent)} 次/8秒），"
                    "正在记录诊断信息，稍后请把日志发出来。"
                )
                diagnostics.report(logger, page, "反复暂停")

        # ---- 关于"播放器跑太快"的处理 ----
        # 之前这里会在开课时把播放位置**往回拖**到平台记录的位置。
        # 实测这是有害的：两次日志里"开课进度不涨"都紧跟在这条回退之后，
        # 而没做过回退的课时（播放器从 0 开始）30 秒内就开始计数。
        # 怀疑站点把"位置突然倒退"当成异常跳转，从而暂停了学习时长计时。
        # 现在改为：开局只管正常播放，等视频播完后由下面的补齐逻辑回退重播。

        # ---- 视频播完但平台进度没记满：回退重播几次 ----
        if duration > 0 and (state.get("ended") or current >= duration - 1.0):
            bridge.sleep(3.0)
            refreshed = catalog_mod.lesson_progress(lesson, catalog)
            if refreshed >= 100:
                return LessonResult(True, paused_seconds, quizzes=quiz_count)
            gained = refreshed - end_progress_mark
            # 上限 8 次；但如果重播一轮后进度几乎没动，说明再放也没用，直接放弃
            if seek_backs >= SEEK_BACK_LIMIT or (
                seek_backs >= 1 and gained < SEEK_BACK_MIN_GAIN
            ):
                logger.warn(
                    f"视频已播完但平台只记录 {refreshed}%（本轮 +{gained}%），放弃重试。"
                )
                return LessonResult(
                    False, paused_seconds, reason=f"进度停在 {refreshed}%",
                    quizzes=quiz_count,
                )
            end_progress_mark = refreshed
            target = duration * max(refreshed, 0) / 100.0 if catalog.progress else duration - 15
            logger.warn(
                f"视频已播完但平台只记录 {refreshed}%，回退到 {target:.0f}s "
                f"继续补齐（第 {seek_backs + 1} 次）。"
            )
            seek(page, min(target, duration - 2))
            last_video_time = target
            last_activity = time.monotonic()
            seek_backs += 1
            continue

        # ---- 卡死判定 ----
        stalled = time.monotonic() - last_activity
        if stalled >= stall_limit:
            recovery_count += 1
            logger.warn(
                f"{stall_limit} 秒没有进度变化，尝试恢复（第 {recovery_count}/3 次）…"
            )
            recover_pause, recover_quizzes = _recover(page, config, logger, bridge, ai)
            paused_seconds += recover_pause
            quiz_count += recover_quizzes
            last_activity = time.monotonic()
            if recovery_count >= 3:
                logger.warn("恢复无效，停止本课时。")
                return LessonResult(
                    False, paused_seconds,
                    reason=f"播放停滞（播放器 {current:.0f}s，平台 {last_catalog_progress}%）",
                    quizzes=quiz_count,
                )

        # 每 30 秒打一条（用分钟刻度去重，否则同一秒会被打两次）
        minute = int(time.time()) % 60
        if minute % 30 == 0 and minute != last_status_minute:
            last_status_minute = minute
            logger.info(
                f"播放中：{current:.0f}s / {duration:.0f}s，平台进度 {last_catalog_progress}%"
                f"（{speed}x，音量 {playback['volume']}）"
            )

        # ---- 「视频在播、平台进度却不涨」的检测与自愈 ----
        # 智慧树在长时间没有任何真实输入事件时可能不上报学习时长，
        # 表现就是播放器正常走、平台进度一直是 0%。这里定时补一轮鼠标操作。
        now_mono = time.monotonic()
        if (
            last_video_time > 5
            and now_mono - progress_changed_at >= PROGRESS_STALL_SECONDS
            and now_mono - last_probe_at >= PROGRESS_PROBE_INTERVAL
        ):
            last_probe_at = now_mono
            logger.warn(
                f"视频在播放，但平台进度已经 {PROGRESS_STALL_SECONDS} 秒没涨"
                f"（停在 {last_catalog_progress}%），模拟鼠标操作尝试唤醒上报。"
            )
            human_activity(page, logger, rounds=4, allow_click=simulate)
            if not stalled_but_counting:
                stalled_but_counting = True
                diagnostics.report(logger, page, "视频在播但进度不涨")

        elapsed_loop = time.time() - loop_started
        if netlog is not None and iteration % 20 == 0:
            netlog.flush_bodies()
        bridge.sleep(max(0.1, POLL_INTERVAL - elapsed_loop))


def _recover(page: Page, config, logger: Logger, bridge: Bridge,
             ai: AiClient | None) -> tuple[float, int]:
    """卡死时的恢复动作，返回耗时秒数。"""
    started = time.time()
    paused, quizzes = handle_interruptions(page, config, logger, bridge, ai)
    if paused > 0:
        return time.time() - started, quizzes
    if popups.dismiss_any_dialog(page, logger):
        bridge.sleep(0.5)
        return time.time() - started, quizzes
    tune(page, config, logger)
    force_play(page, logger, gesture=True)
    bridge.sleep(1.5)
    return time.time() - started, quizzes
