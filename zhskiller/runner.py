"""流程编排：开浏览器 -> 登录 -> 逐门课程 -> 逐个未完成课时。"""

from __future__ import annotations

import time

from playwright.sync_api import Error as PlaywrightError, Page

from . import browser as browser_mod
from . import catalog as catalog_mod
from . import captcha, exam, login, popups, quiz, site
from .ai import AiClient
from .bridge import Bridge, Stopped
from .logger import LOG_RETENTION_DAYS, Logger, cleanup_old_logs
from .netlog import NetworkLog
from . import player as player_mod
from .player import watch_lesson

LESSON_CLICK_TIMEOUT = 10_000
# 同一条章节测验最多试几次（章节列表收起 / 平台网络波动都值得原样重试）
MAX_CHAPTER_TEST_TRIES = 3
# 同一条测验最多点几次（中间会把随堂练习等遮挡物清掉再点）
MAX_CHAPTER_CLICK_TRIES = 3
# 「每门课刷够 N 分钟」时最多复习几轮（防止目标设得过大时无限循环）
MAX_REWATCH_ROUNDS = 6


class Runner:
    def __init__(self, config, bridge: Bridge):
        self.config = config
        self.bridge = bridge
        self.logger = Logger(bridge)
        self.ai = AiClient(config.data["quiz"]["ai"], self.logger)
        self.session = None
        self.netlog: NetworkLog | None = None
        # 运行统计（结束时打一份摘要，方便对照"还剩多少没刷完"）
        self.stat_lessons_done = 0
        self.stat_lessons_failed = 0
        self.stat_quizzes = 0
        self.stat_courses_skipped = 0

    # ------------------------------------------------------------------
    def run(self) -> bool:
        config = self.config
        bridge = self.bridge
        logger = self.logger

        if not config.courses:
            logger.error("还没有配置课程链接。请在界面上填入课程播放页地址。")
            return False

        # 顺手清掉过期的日志与页面导出，别让 data/logs 越积越大
        try:
            removed_logs = cleanup_old_logs()
            if removed_logs:
                logger.info(
                    f"已清理 {removed_logs} 个过期日志文件"
                    f"（只保留最近 {LOG_RETENTION_DAYS} 天）。"
                )
        except Exception:
            pass

        session = None
        ok = True
        started_at = time.time()
        try:
            # 登录方式（是否用程序内保存的账号）必须在开浏览器之前问清楚，
            # 否则弹出浏览器、加载登录页会和用户的点击互相干扰。
            logger.section("登录设置")
            plan = login.prepare_login(config, logger, bridge)
            bridge.check_stop()

            session = browser_mod.open_session(config, logger)
            self.session = session
            if not login.ensure_login(
                session, config, logger, bridge, plan, probe_url=config.courses[0]
            ):
                logger.error("登录未完成，程序退出。")
                return False
            logger.success("登录状态就绪，开始学习。")
            if config.get("run_mode") != "chapter_only":
                # 上一轮如果是「仅章节测验」，可能在这个页面里留下了"视频保持暂停"
                # 的看门狗（接管模式下页面是同一个），刷视频前先撤掉。
                player_mod.release_keep_video_paused(session.page)

            if config.get("browser.same_tab_redirect", False):
                browser_mod.install_same_tab_redirect(session.page)
                logger.warn(
                    "已开启「拦截 window.open」——如果章节测验打不开，请把它关掉"
                    "（它会干扰站点的开考流程）。"
                )

            if config.get("diagnostics.capture_network", True):
                self.netlog = NetworkLog(logger, enabled=True)
                self.netlog.attach(session.page)
                logger.info(f"网络诊断已开启：{self.netlog.path}")

            for index, url in enumerate(config.courses, 1):
                bridge.check_stop()
                chapter_only = config.get("run_mode") == "chapter_only"
                logger.section(
                    f"课程 {index}/{len(config.courses)}"
                    + ("（仅章节测验）" if chapter_only else "")
                )
                bridge.status(f"课程 {index}/{len(config.courses)}：准备中")
                if chapter_only:
                    ok_course = self._run_chapter_tests(
                        session.page, url, index, len(config.courses)
                    )
                else:
                    ok_course = self._run_course(
                        session.page, url, index, len(config.courses)
                    )
                if not ok_course:
                    ok = False
        except Stopped:
            logger.warn("已按你的要求停止。")
            ok = False
        except PlaywrightError as exc:
            # 用户手动关掉浏览器窗口时，Playwright 会抛 TargetClosedError；
            # 这种情况不该打印一大坨堆栈吓人，说清楚就行。
            if "has been closed" in str(exc) or "TargetClosed" in type(exc).__name__:
                logger.error("浏览器窗口已关闭，本次运行结束。")
            else:
                logger.exception("运行时出现未处理异常。", exc)
            ok = False
        except Exception as exc:
            logger.exception("运行时出现未处理异常。", exc)
            ok = False
        finally:
            config.save()
            if self.netlog is not None:
                try:
                    self.netlog.flush_bodies(limit=40)
                    self.netlog.detach(session.page if session else None)
                    logger.info(self.netlog.summary())
                except Exception:
                    pass
                self.netlog = None
            logger.section("运行摘要")
            minutes = max(0.0, time.time() - started_at) / 60
            logger.info(f"总用时：{minutes:.1f} 分钟")
            logger.info(
                f"已完成课时：{self.stat_lessons_done} 个；"
                f"未确认完成：{self.stat_lessons_failed} 个"
            )
            if config.get("run_mode") == "chapter_only":
                logger.info("本次运行模式：仅完成章节测验（没有观看视频）")
            logger.info(f"自动处理随堂练习：{self.stat_quizzes} 次")
            if self.stat_courses_skipped:
                logger.info(f"整门已学完而跳过的课程：{self.stat_courses_skipped} 门")
            logger.info("提示：智慧树的服务端进度每 2~3 分钟才同步一次，页面上的数字是本地计算的。")
            self.session = None
            if session is not None:
                try:
                    from . import cookies as cookies_mod

                    cookies_mod.save(session.context)
                except Exception:
                    pass
                session.close()
        return ok

    # ------------------------------------------------------------------
    def _run_course(self, page: Page, url: str, index: int, total: int) -> bool:
        config = self.config
        bridge = self.bridge
        logger = self.logger

        logger.info(f"打开课程页：{url}")
        login.safe_goto(page, url, logger)
        catalog = None
        for attempt in range(2):
            catalog = catalog_mod.detect_with_recovery(
                page, url, config, logger, bridge, timeout=90
            )
            if catalog is not None:
                break
            if not login.is_login_page(page.url):
                break
            logger.warn("课程页要求重新登录，请在弹出的提示里完成登录。")
            if not login.manual_login_here(self.session, config, logger, bridge):
                return False
            login.safe_goto(page, url, logger)
        if catalog is None:
            logger.error("无法识别课程目录，可能是暂未适配的课程版本。")
            logger.info("请把课程链接发给开发者，或在浏览器中确认页面是否正常加载。")
            return False
        logger.success(f"识别到课程目录类型：{catalog.label}")

        if catalog.key == "fusion":
            catalog_mod.expand_folds(page, logger)

        popups.close_announcements(page, logger)
        catalog_mod.ensure_visible(page, catalog, logger)

        name = catalog_mod.course_title(page, catalog)
        if name:
            logger.info(f"当前课程：{name}")
        if captcha.has_verification(page):
            captcha.handle_verification(page, config, logger, bridge)

        lessons = catalog_mod.list_lessons(page, catalog)
        if not lessons:
            logger.error("目录里没有找到任何视频课时。")
            return False
        todo = catalog_mod.unfinished(lessons, catalog)
        logger.info(f"共 {len(lessons)} 个课时，其中未完成 {len(todo)} 个。")

        target_minutes = float(config.get("playback.min_minutes_per_course", 0) or 0)
        rewatch = False
        if not todo:
            if target_minutes > 0:
                logger.info(
                    f"本课程视频都已完成，但设置了「每门课刷够 {target_minutes:.0f} 分钟」，"
                    "将重看已完成的课时来凑学习时长。"
                )
                todo = lessons
                rewatch = True
            elif not config.data["playback"]["review_when_finished"]:
                logger.success("本课程所有视频进度都已完成，跳过。")
                self.stat_courses_skipped += 1
                return True
            else:
                logger.info("本课程已完成，按设置从头复习一遍。")
                todo = lessons
                rewatch = True

        course_start = time.time()
        paused_total = 0.0
        ok = True
        round_index = 0
        queue = list(todo)
        queue_rewatch = rewatch
        course_limit_hit = False
        target_reached = False
        while True:
            round_index += 1
            if round_index > 1:
                logger.info(
                    f"本门课本次只学了 "
                    f"{self._course_minutes(course_start, paused_total):.1f} 分钟，"
                    f"还不到 {target_minutes:.0f} 分钟，再复习一轮（第 {round_index} 轮）。"
                )
            for position, lesson in enumerate(queue, 1):
                bridge.check_stop()
                if queue_rewatch:
                    bridge.status(
                        f"课程 {index}/{total}：复习第 {round_index} 轮 "
                        f"{position}/{len(queue)}"
                    )
                else:
                    bridge.status(
                        f"课程 {index}/{total}：第 {position}/{len(queue)} 个课时"
                    )
                result, title = self._play_one(
                    page, lesson, catalog, position, len(queue), course_start,
                    paused_total, rewatch=queue_rewatch,
                )
                paused_total += result.paused_seconds
                if result.reached_limit:
                    logger.warn("本门课程已达到设置的学习时限，转入下一门课程。")
                    course_limit_hit = True
                    break
                if result.lesson_limit:
                    logger.info(f"「{title}」达到单个视频时限，继续下一个课时。")
                    continue
                if not result.completed:
                    ok = False
                    self.stat_lessons_failed += 1
                    logger.warn(f"「{title}」未确认完成：{result.reason}")
                    logger.info("继续尝试下一个课时。")
                else:
                    self.stat_lessons_done += 1
                    logger.success(f"「{title}」已完成。")
                self._chapter_quiz(page)
                # 刷够时长：每看完一个课时就检查一次，够了就结束本门课
                if target_minutes > 0:
                    elapsed = self._course_minutes(course_start, paused_total)
                    if elapsed >= target_minutes:
                        logger.success(
                            f"本门课程本次已学 {elapsed:.1f} 分钟，"
                            f"达到「每门课刷够 {target_minutes:.0f} 分钟」的要求。"
                        )
                        target_reached = True
                        break
            if course_limit_hit or target_reached:
                break
            if target_minutes <= 0:
                break
            if round_index >= MAX_REWATCH_ROUNDS:
                logger.warn(
                    f"已复习 {round_index} 轮仍未达到 {target_minutes:.0f} 分钟，"
                    "先跳到下一门课（可以把目标调小一点再试）。"
                )
                break
            # 一轮走完还不够时长：下一轮把「全部课时（含已完成的）」再放一遍
            queue = list(lessons)
            queue_rewatch = True

        minutes = max(0.0, time.time() - course_start - paused_total) / 60
        logger.info(f"本课程本次学习用时约 {minutes:.1f} 分钟。")
        return ok

    @staticmethod
    def _course_minutes(course_start: float, paused_total: float) -> float:
        """本门课程本次实际学习的分钟数（扣掉被弹窗/验证码打断的时间）。"""
        return max(0.0, time.time() - course_start - paused_total) / 60

    # ------------------------------------------------------------------
    def _play_one(self, page: Page, lesson, catalog: site.Catalog, position: int,
                  total: int, course_start: float, paused_before: float,
                  rewatch: bool = False):
        """点击并播放一个课时（rewatch=True 表示复习/凑时长，会把视频从头重播）。"""
        config = self.config
        bridge = self.bridge
        logger = self.logger

        if not catalog_mod.click_lesson(page, lesson, logger):
            logger.debug("第一次点击课时未成功，稍后重试。")
        if not catalog_mod.wait_active(lesson, catalog):
            logger.warn("课时切换较慢，重试一次。")
            catalog_mod.click_lesson(page, lesson, logger)
            catalog_mod.wait_active(lesson, catalog)

        bridge.sleep(1.0)
        title = catalog_mod.lesson_title(page, lesson, catalog)
        before = catalog_mod.lesson_progress(lesson, catalog)
        logger.info(f"[{position}/{total}] 开始学习：{title}（当前平台进度 {before}%）")
        if rewatch:
            logger.info("（复习模式：这一遍会从头重新播放，按「放完一遍」判定完成）")
        bridge.progress(
            lesson=title, lesson_index=position, lesson_total=total, lesson_progress=before
        )

        result = watch_lesson(
            page, lesson, catalog, config, logger, bridge, self.ai,
            course_start, paused_before, netlog=self.netlog, rewatch=rewatch,
        )
        self.stat_quizzes += result.quizzes
        if result.quizzes:
            logger.info(f"本课时自动处理了 {result.quizzes} 次随堂练习。")
        return result, title

    # ------------------------------------------------------------------
    def _chapter_quiz(self, page: Page) -> None:
        """章节结束后的练习/测试。

        注意：这里必须严格区分「章节测验」和「随堂练习」。
        早先直接用 quiz.has_quiz()，把每个视频都有的随堂练习面板也认成了章节测验，
        在用户没勾选自动完成时白白弹窗询问；而那个面板未作答是关不掉的，
        用户点「跳过」之后页面就卡住了。
        """
        config = self.config
        bridge = self.bridge
        logger = self.logger
        bridge.sleep(1.5)
        if not quiz.is_chapter_quiz(page):
            return

        if not config.data["quiz"]["auto_chapter_quiz"]:
            # 没勾选就只在日志里说明，不弹窗打扰
            logger.info("检测到章节测验，但「自动完成章节测验」未勾选，已跳过（不会替你作答）。")
            return

        mode = config.quiz_mode("chapter")
        # 手动作答时不存在"自动提交"这回事，界面上也把那个勾选灰掉了，这里再兜一层
        auto_submit = bool(config.get("quiz.auto_submit_chapter", False)) and mode != "manual"
        logger.info(
            f"检测到章节测验，按设置自动作答"
            f"（作答方式 {mode}，"
            f"完成后自动提交：{'是' if auto_submit else '否'}）。"
        )
        quiz.handle_quiz(
            page, config, logger, bridge, self.ai, "chapter", "章节测验",
            auto_submit=auto_submit,
        )

    # ------------------------------------------------------------------
    def _run_chapter_tests(self, page: Page, url: str, index: int, total: int) -> bool:
        """「仅完成章节测验」模式：不看视频，只处理目录里的章节测验。"""
        config = self.config
        bridge = self.bridge
        logger = self.logger

        logger.info(f"打开课程页：{url}")
        login.safe_goto(page, url, logger)
        catalog = None
        for attempt in range(2):
            catalog = catalog_mod.detect_with_recovery(
                page, url, config, logger, bridge, timeout=90
            )
            if catalog is not None:
                break
            if not login.is_login_page(page.url):
                break
            # 登录态其实已经失效（比如上次留下的 cookie 已过期），
            # 这里必须让用户真正登录完成，而不是直接失败退出。
            logger.warn("课程页要求重新登录，请在弹出的提示里完成登录。")
            if not login.manual_login_here(self.session, config, logger, bridge):
                return False
            login.safe_goto(page, url, logger)
        if catalog is None:
            logger.error("无法识别课程目录，无法列出章节测验。")
            return False
        if catalog.key == "fusion":
            catalog_mod.expand_folds(page, logger)
        catalog_mod.ensure_visible(page, catalog, logger)
        popups.close_announcements(page, logger)
        # 仅答题模式下不要把视频留在后台播放：它过一会儿会弹随堂练习，
        # 而随堂练习弹出时会把章节列表收起来，干扰后面找目录。
        _pause_background_video(page, logger)
        # 光按一次暂停不够（站点会自己续播），这里装一个看门狗持续压制。
        player_mod.install_keep_video_paused(page)
        logger.info("仅答题模式：已让页面视频保持暂停，不会再弹随堂练习。")
        if captcha.has_verification(page):
            captcha.handle_verification(page, config, logger, bridge)

        selector = catalog_mod.chapter_item_selector(page)
        if not selector:
            logger.info(
                "目录里没有找到章节测验条目（.item-test）。"
                "如果这门课确实有章节测验，请把课程目录的 HTML 发我以便适配。"
            )
            return True

        total_items = page.locator(selector).count()
        logger.info(f"目录里找到 {total_items} 个章节测验条目。")
        if not config.data["quiz"]["auto_chapter_quiz"]:
            for position in range(total_items):
                info = catalog_mod.chapter_item_info(page.locator(selector).nth(position))
                logger.info(
                    f"  [{position + 1}] {info['title']}"
                    f" | {'已完成' if info['done'] else '未完成'}"
                    f" | class={info['classes'][:60]!r} | 标记={info['marks']}"
                )
            logger.info("「自动完成章节测验」未勾选，本轮只做清点，不做任何作答。")
            return True

        mode = config.quiz_mode("chapter")
        auto_submit = bool(config.get("quiz.auto_submit_chapter", False)) and mode != "manual"
        # 复查：进入"完成率 100% 但未提交"或"做到一半"的测验逐题重选。
        # 仅当勾了复查、且 AI 接口可用时生效（否则没有可靠的重新作答依据）。
        review = bool(config.get("quiz.review_chapter", False)) and self.ai.ready
        if config.get("quiz.review_chapter", False) and not self.ai.ready:
            logger.warn("勾选了「复查答题结果」，但 AI 接口不可用，本次不复查。")
        logger.info(
            f"开始处理章节测验（作答方式 {mode}，完成后自动提交："
            f"{'是' if auto_submit else '否'}，复查：{'是' if review else '否'}）。"
        )

        handled_count = 0
        skipped_count = 0
        failed_count = 0
        blocked_count = 0
        aborted = False
        processed: set[str] = set()
        for position in range(total_items):
            bridge.check_stop()
            info = catalog_mod.chapter_item_info(page.locator(selector).nth(position))
            # 所有章节测验名字都叫「测试」，用"所属章节 + 名字"做稳定标识，
            # 避免页面重新渲染后按序号点错（之前出现过"从第一章跳到第四章"）
            key = f"{info.get('chapter', '')}|{info['title']}"
            if key in processed:
                logger.info(f"[{position + 1}/{total_items}] {key} 本轮已处理过，跳过。")
                continue
            # 日志里把所属章节写出来，方便对照"现在是第几章的测试"
            label = f"{info.get('chapter') or ''}{info['title']}".strip() or "章节测验"
            bridge.status(
                f"课程 {index}/{total}：章节测验 {position + 1}/{total_items}"
            )
            if info["done"] and not review:
                logger.info(
                    f"[{position + 1}/{total_items}] 「{label}」已完成"
                    f"（{info.get('status') or '状态标记'}），跳过。"
                )
                continue
            processed.add(key)
            if position == 0 and info.get("html"):
                logger.info(f"（首个条目的 HTML 片段，用于核对结构：{info['html'][:280]}）")

            # 同一条目最多试 3 次。失败基本是两类原因：章节列表被收起导致点不动、
            # 或者平台网络波动导致试卷页打不开 —— 这两种都值得原样重试，
            # 而不是直接跳到下一章（用户看到的就是"从第四章跳到第六章"）。
            for attempt in range(1, MAX_CHAPTER_TEST_TRIES + 1):
                bridge.check_stop()
                if attempt > 1:
                    logger.info(
                        f"[{position + 1}/{total_items}] 第 {attempt} 次尝试「{label}」…"
                    )
                    bridge.sleep(2.0 * (attempt - 1))
                else:
                    logger.info(f"[{position + 1}/{total_items}] 打开：{label}")
                outcome = self._run_one_chapter_test(
                    page, url, catalog, selector, position, label,
                    config, logger, bridge, auto_submit, review,
                )
                if outcome == "aborted":
                    aborted = True
                    break
                if outcome == "done":
                    handled_count += 1
                    break
                if outcome == "skipped":
                    skipped_count += 1
                    break
                if outcome == "blocked":
                    blocked_count += 1
                    logger.warn(
                        f"「{label}」点开后没有任何反应，先跳过它"
                        "（这类测验通常要先看完对应章节的视频，下次运行会再试）。"
                    )
                    break
                # failed：收拾一下现场，准备重试同一条目
                self._recover_after_failed_test(page, url, catalog, logger, bridge)
            else:
                failed_count += 1
                logger.error(
                    f"「{label}」连续 {MAX_CHAPTER_TEST_TRIES} 次都没能打开或识别，"
                    "先跳过它继续后面（这一条下次运行会再试）。"
                )
            if aborted:
                break

        if aborted:
            login.safe_goto(page, url, logger)
            return False
        logger.info(
            f"章节测验处理结束：本次实际作答 {handled_count} 个"
            f"（跳过已完成 {skipped_count} 个，打不开 {blocked_count} 个，"
            f"失败 {failed_count} 个，共 {total_items} 个条目）。"
        )
        return True

    def _run_one_chapter_test(self, page: Page, url: str, catalog, selector: str,
                              position: int, label: str, config, logger: Logger,
                              bridge: Bridge, auto_submit: bool,
                              review: bool) -> str:
        """打开并处理一条章节测验。返回 done / skipped / failed / aborted。"""
        before_pages = len(self.session.context.pages) if self.session else 1
        target = page
        opened = False
        for click_try in range(1, MAX_CHAPTER_CLICK_TRIES + 1):
            bridge.check_stop()
            # 每轮都重新装一次看门狗（页面跳转/刷新后会失效），
            # 再把挡在前面的随堂练习等弹层清掉 —— 它们盖住目录时点击是没反应的。
            player_mod.install_keep_video_paused(page)
            self._clear_in_video_quiz(page, config, logger, bridge)
            popups.dismiss_any_dialog(page, logger)

            item = page.locator(selector).nth(position)
            if not self._prepare_chapter_item(page, catalog, item, logger):
                logger.warn(
                    f"目录里的「{label}」现在点不到（章节列表多半被收起来了），稍后重试。"
                )
                return "failed"

            try:
                before_url = page.url
            except Exception:
                before_url = url
            catalog_mod.click_lesson(page, item, logger)
            target = self._wait_for_exam_open(
                page, before_pages, logger, bridge,
                timeout=25 if click_try == 1 else 15,
            )

            # 这一下到底有没有打开"答题相关"的东西？点了完全没反应时（有些测验
            # 要求先看完对应章节的视频），绝不能退回去处理课程页上的东西 ——
            # 之前就是把视频里的随堂练习当成章节测验答了，然后卡在那里出不来。
            opened = (
                target is not page
                or exam.is_exam_page(target)
                or quiz.is_chapter_quiz(target)
            )
            if not opened and target is page:
                try:
                    opened = target.url != before_url
                except Exception:
                    opened = False
            if opened:
                break
            if click_try < MAX_CHAPTER_CLICK_TRIES:
                logger.warn(
                    f"第 {click_try} 次点击「{label}」没有反应，"
                    "清掉页面上挡着的东西后再点一次…"
                )
                bridge.sleep(1.0)

        if not opened:
            logger.warn(
                f"点了「{label}」之后页面没有任何反应"
                "（既没跳转、也没开新标签页、也没出现测验弹窗）。"
            )
            if quiz.is_visible_panel(page):
                logger.warn(
                    "页面上这个是视频里的「随堂练习」，不是章节测验，先把它关掉再重试。"
                )
                self._clear_in_video_quiz(page, config, logger, bridge)
            # 点两次都毫无反应，基本可以断定这一条现在打不开（多数是要求先看完
            # 对应章节的视频）。不要在这里反复重试，也不要去动课程页上的东西。
            return "blocked"

        if target is not page:
            logger.info("章节测验在新标签页中打开，已切换过去处理。")
            if self.netlog is not None:
                self.netlog.attach(target)
            # 故意不调用 bring_to_front：那会把浏览器窗口强行弹到最前面，
            # 用户在忙别的事情时会被打断。Playwright 在后台标签页上照样
            # 能点击和读取（我们已经关了 Chrome 的后台节流）。
        else:
            logger.debug(f"章节测验在当前页打开：{page.url[:90]}")

        # 新标签页刚打开时页面还在"正在加载中"，试卷是异步渲染的。
        # 不先等它渲染出来，就会误判成"没识别到题目结构"并跳过一个正常的测验。
        exam.wait_ready(target, logger, bridge, timeout=30)

        # 如果被踢到登录页，说明这个标签页拿不到登录态（站点把凭证放在
        # sessionStorage 里，不跨标签页共享）。继续往下只会不停开登录页，
        # 所以立刻停下来并说明原因。
        if login.is_login_page(target.url):
            logger.error(f"打开的章节测验被跳到了登录页：{target.url[:90]}")
            logger.info("该标签页拿不到登录态，已停止继续打开章节测验（否则会不停地弹登录页）。")
            logger.info(
                "建议：改用「接管已打开的浏览器」模式（用同一个窗口打开章节测验），"
                "或者先在浏览器里手动把这一门课的章节测验打开一次确认能正常进入。"
            )
            if target is not page:
                try:
                    target.close()
                except Exception:
                    pass
            return "aborted"

        # 章节测验有两种形态：整页试卷（在线考试页）和弹窗。
        # 先按整页试卷处理，识别不到再退回弹窗逻辑。
        result = exam.handle_exam_page(
            target, config, logger, bridge, self.ai, "chapter",
            auto_submit, review=review,
        )
        if result == "skipped":
            logger.info("该测验已完成，跳过（不需要作答）。")
            self._leave_exam_tab(target, page, url, logger, keep=False)
            return "skipped"
        done = bool(result)
        if not done:
            if quiz.is_chapter_quiz(target):
                done = bool(quiz.handle_quiz(
                    target, config, logger, bridge, self.ai, "chapter", "章节测验",
                    auto_submit=auto_submit,
                ))
            elif quiz.is_visible_panel(target):
                # 视频里的随堂练习：不计分，但会挡住页面，答完关掉即可
                logger.warn(
                    "这里显示的是视频里的「随堂练习」而不是章节测验，先把它答掉关掉。"
                )
                self._clear_in_video_quiz(target, config, logger, bridge)
        if done:
            # 收尾：勾了自动提交 → 关掉答题标签页；
            #       没勾（只作答交给用户核对）→ 保留标签页，
            #       否则页面一关用户就没法核对/提交了（答案已经点「保存」存到服务器）。
            self._leave_exam_tab(target, page, url, logger, keep=not auto_submit)
            if captcha.has_verification(page):
                captcha.handle_verification(page, config, logger, bridge)
            return "done"

        logger.warn(
            "打开后没有识别到题目结构（可能是整页试卷、网络异常或结构未适配）。"
        )
        # 自动导出真实页面，省得手动复制（手动复制经常只拿到 head）
        from . import diagnostics

        diagnostics.report_structure(target, logger, "未识别的测验页")
        diagnostics.dump_page(target, logger, "unknown-exam")
        # 出问题的那一页直接关掉：留着它既不能作答，也会挡住后面重试的标签页
        self._leave_exam_tab(target, page, url, logger, keep=False)
        return "failed"

    def _clear_in_video_quiz(self, page: Page, config, logger: Logger,
                             bridge: Bridge) -> bool:
        """把视频里弹的随堂练习答掉关掉（它不是章节测验，也不计分）。

        仅章节测验模式下本来不该出现它（视频被看门狗按住了），但用户手动
        播放过、或者视频在装看门狗之前就开播了，都可能让面板先弹出来。
        不处理的话它会一直盖在上面，点目录条目全都没反应。
        """
        try:
            if quiz.is_chapter_quiz(page):
                return False          # 这是章节测验弹窗，交给正常流程处理
            if not (quiz.is_visible_panel(page) or quiz.has_quiz(page)):
                return False
        except Exception:
            return False
        logger.warn("检测到视频里的随堂练习（不计分，但会挡住页面），先把它答掉关掉…")
        try:
            quiz.handle_quiz(
                page, config, logger, bridge, self.ai, "in_video", "随堂练习",
                auto_submit=True,
            )
        except Exception as exc:
            logger.warn(f"处理随堂练习时出错：{exc}")
        return True

    def _prepare_chapter_item(self, page: Page, catalog, item, logger: Logger) -> bool:
        """确认这个目录条目真的能点到（章节列表被收起时先展开）。"""
        for attempt in range(3):
            try:
                if item.is_visible():
                    box = item.bounding_box()
                    if box and box.get("width", 0) > 2 and box.get("height", 0) > 2:
                        return True
            except Exception:
                pass
            catalog_mod.ensure_visible(page, catalog, logger)
            try:
                page.wait_for_timeout(500)
            except Exception:
                return False
        # 最后再硬点一次展开按钮
        catalog_mod.expand_catalog(page, logger)
        try:
            page.wait_for_timeout(700)
            return bool(item.is_visible())
        except Exception:
            return False

    def _recover_after_failed_test(self, page: Page, url: str, catalog,
                                   logger: Logger, bridge: Bridge) -> None:
        """一次章节测验失败后收拾现场，为重试同一条目做准备。"""
        try:
            if page.url != url:
                login.safe_goto(page, url, logger)
                bridge.sleep(2.0)
        except Exception:
            pass
        # 重新跳回课程页后，先把"视频保持暂停"的看门狗装回去
        player_mod.install_keep_video_paused(page)
        try:
            popups.dismiss_any_dialog(page, logger)
        except Exception:
            pass
        catalog_mod.ensure_visible(page, catalog, logger)
        if page.locator(catalog_mod.chapter_item_selector(page) or ".item-test").count() == 0:
            catalog_mod.detect_with_recovery(
                page, url, self.config, logger, bridge, timeout=30
            )

    def _wait_for_exam_open(self, page: Page, before_pages: int,
                            logger: Logger, bridge: Bridge, timeout: float = 30.0):
        """点完章节测验后，等它真的打开（可能是新标签页，也可能当前页跳转）。

        实测：考试接口会被调用，但页面有时并不会马上跳走，
        固定 sleep 3 秒就去判断结构会误判成"没打开"。所以这里轮询等待：
          · 出现新标签页 → 用新标签页
          · 当前页 URL 变了 → 用当前页
          · 当前页出现了试卷元素 → 用当前页
        另外站点大量使用 closed shadow root，试卷有可能不是普通 DOM，
        所以还额外看一眼标签页地址里有没有考试关键字。
        """
        deadline = time.time() + timeout
        last_url = page.url
        seen_new_pages: list = []
        round_index = 0
        while time.time() < deadline:
            bridge.check_stop()
            round_index += 1
            if self.session:
                pages = self.session.context.pages
                for candidate in pages[before_pages:]:
                    if candidate in seen_new_pages:
                        continue
                    seen_new_pages.append(candidate)
                    logger.info(f"检测到新标签页：{candidate.url[:100]}")
                    try:
                        candidate.wait_for_load_state("domcontentloaded", timeout=8000)
                    except Exception:
                        pass
                # 新标签页优先，但要确认它真的像试卷页
                for candidate in pages[before_pages:]:
                    if self._looks_like_exam(candidate):
                        return candidate
            try:
                if page.url != last_url:
                    logger.debug(f"章节测验跳转到：{page.url[:100]}")
                    # 给新页面一点加载时间
                    bridge.sleep(2.0)
                    return page
                if self._looks_like_exam(page):
                    return page
            except Exception:
                pass
            # 站点大量使用 closed shadow root，普通 DOM 可能查不到试卷；
            # 每隔几轮用无障碍树看一次页面文字，能发现"试卷其实已经打开"。
            if round_index % 5 == 0:
                from . import diagnostics

                targets = [page]
                if self.session:
                    targets = list(self.session.context.pages)[before_pages:] or [page]
                for candidate in targets:
                    try:
                        if diagnostics.looks_like_exam_via_accessibility(candidate):
                            logger.info(
                                "通过无障碍树确认试卷页已打开"
                                f"（{candidate.url[:90]}）。"
                            )
                            return candidate
                    except Exception:
                        continue
            bridge.sleep(0.8)
        logger.warn(
            "点击章节测验后等了 25 秒，页面既没跳转也没出现新标签页。"
        )
        try:
            patched = page.evaluate("() => Boolean(window.__zhsSameTabPatched)")
        except Exception:
            patched = "未知"
        pages = self.session.context.pages if self.session else []
        logger.warn(
            f"诊断：新标签页补丁={patched}，标签页数={len(pages)}，"
            f"地址={[p.url[:70] for p in pages]}"
        )
        logger.warn(
            f"当前地址：{page.url[:120]}；"
            "请确认这个「测试」条目是否需要先满足某些条件（比如前置视频没看完）。"
        )
        return page

    def _looks_like_exam(self, target: Page) -> bool:
        """这个标签页像不像试卷页（普通 DOM 或地址特征）。"""
        from . import exam as exam_mod

        try:
            if exam_mod.is_exam_page(target):
                return True
        except Exception:
            pass
        try:
            url = (target.url or "").lower()
        except Exception:
            return False
        if "about:blank" in url or not url.startswith("http"):
            return False
        if "login" in url:
            return False
        return "exam" in url or "studentexam" in url or "testpaper" in url

    def _leave_exam_tab(self, target: Page, page: Page, url: str,
                        logger: Logger, keep: bool) -> None:
        """离开答题标签页。

        keep=True 时保留（未勾自动提交，留给用户核对/提交）；
        keep=False 时关掉（或从当前页回到课程目录）。
        """
        if target is not page:
            if keep:
                logger.info(
                    "已保留这个答题标签页（未自动提交），你核对后点「提交作业」即可。"
                )
            else:
                try:
                    target.close()
                except Exception:
                    pass
            # 这里同样不 bring_to_front：不去抢用户的焦点。
            # （需要点课程页时，Playwright 直接在那个标签页上操作即可。）
            return
        # 试卷就开在当前标签页，必须离开才能继续看目录
        login.safe_goto(page, url, logger)
        self.bridge.sleep(2.0)


def _pause_background_video(page: Page, logger: Logger) -> None:
    """把后台还在放的课程视频按停。

    仅答题模式下我们不需要看视频，但它留在那里播着，过一会儿会弹随堂练习；
    随堂练习一弹出来站点就把章节列表收起来，影响后面找目录。
    """
    try:
        state = page.evaluate(
            """() => {
                const v = document.querySelector('video');
                if (!v) return null;
                if (!v.paused) { v.pause(); return false; }
                return true;
            }"""
        )
    except Exception:
        return
    if state is False:
        logger.info("已暂停后台播放中的课程视频（仅答题模式不需要播放）。")
