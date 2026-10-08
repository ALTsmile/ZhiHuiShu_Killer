"""智慧树站点的选择器与常量。

这里是全项目唯一写死页面结构的地方。智慧树改版时原则上只需要改这个文件。
选择器来源：参考开源项目 CXRunfree/Autovisor（2026-09 实测）+ 本项目实测校准。
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 域名
# ---------------------------------------------------------------------------
LOGIN_URL = "https://passport.zhihuishu.com/login"
LOGIN_HOSTS = {"login.zhihuishu.com", "passport.zhihuishu.com"}
COOKIE_URLS = [
    "https://www.zhihuishu.com",
    "https://passport.zhihuishu.com",
    "https://onlineweb.zhihuishu.com",
    "https://studyvideoh5.zhihuishu.com",
    "https://studywisdomh5.zhihuishu.com",
    "https://fusioncourseh5.zhihuishu.com",
    "https://hike.zhihuishu.com",
]

# ---------------------------------------------------------------------------
# 登录页
# ---------------------------------------------------------------------------
LOGIN_USERNAME = "#lUsername, input[name='mobile'], input[placeholder*='手机号'], input[placeholder*='账号']"
LOGIN_PASSWORD = "#lPassword, input[type='password']"
LOGIN_SUBMIT = ".wall-sub-btn, .btn-block__grandient_login, button[type='submit'], .login-btn"
LOGIN_AGREE = "input.el-checkbox__original, .agree-box .el-checkbox__input"
# 登录方式 tab（实测四个页签）：账号登录 / 学号登录 / 工号登录 / 扫码
LOGIN_TABS = "div.el-tabs__item"
LOGIN_TAB_STUDENT = "#tab-2"
LOGIN_PANE_STUDENT = "#pane-2"

# ---------------------------------------------------------------------------
# 人机验证（网易易盾 / 腾讯验证码）
# ---------------------------------------------------------------------------
VERIFY_POPUP_SELECTORS = (
    ".yidun_popup .yidun_modal",
    ".yidun_modal__title",
    ".yidun_intelli-tips",
    "[id^='tcaptcha_transform']",
    "#captcha_iframe",
)
YIDUN_BG = "img.yidun_bg-img"
YIDUN_JIGSAW = "img.yidun_jigsaw"
YIDUN_SLIDER = "div.yidun_slider"
# 易盾的加载态实测是 .yidun_loadbox / .yidun_loadicon（不是 .yidun--loading）
YIDUN_LOADING = "div.yidun_loadbox, div.yidun_loadicon, div.yidun--loading"
# 验证通过后易盾会把票据回填到这个隐藏输入框
YIDUN_VALIDATE_INPUT = "input[name='NECaptchaValidate'], .yidun_input"

# ---------------------------------------------------------------------------
# 目录结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Catalog:
    """一种课程页面布局的目录结构描述。"""

    key: str
    label: str
    item: str                    # 课时节点
    active: str                  # 当前播放中的课时
    finish: str                  # 课时完成标记（存在即视为 100%）
    title: str                   # 课时标题
    active_class: str
    progress: str | None = None  # 进度条
    progress_attr: str | None = None  # 进度属性（None 表示读文本）
    course_title: str | None = None


# 说明：以下选择器是从 studyywisdomh5 的前端 bundle 里提取的真实类名校准的，
# 例如目录项的类名来自编译后的模板片段
#   class: ["child-info", {current: videoId == 当前id, cur: videoId, hasvideo: 有视频}]
# 完成标记来自
#   isStudiedLesson===1 || (isStudiedLesson===2 && percentage>=80) ? <icon class="child-check">
# 也就是说 child-check 在 80% 就会出现，所以必须优先读进度环而不是看勾。
WISDOM = Catalog(
    key="wisdom",
    label="智慧共享课",
    item=".child-info.hasvideo",
    active=".child-info.hasvideo.current",
    finish=".child-check",
    title=".child-name",
    active_class="current",
    progress="[role='progressbar'][aria-valuenow]",
    progress_attr="aria-valuenow",
    course_title=".course-name",
)

# 目录里还有一层「父子小节」：父小节是 .child-info，子小节是 .child-info.son，
# 两者都带 hasvideo，都会被 item 选择器覆盖，顺序也符合播放顺序。

FUSION = Catalog(
    key="fusion",
    label="融合共享课",
    item=".chapter-item",
    active=".chapter-content-second.current",
    finish=".finish-icon",
    title=".item-name",
    active_class="current",
    progress=".el-progress",
    progress_attr="aria-valuenow",
    course_title=".course-name",
)

HIKE = Catalog(
    key="hike",
    label="在线学堂（新版）",
    item=".file-item",
    active=".file-item.active",
    finish=".icon-finish",
    title="span[title]",
    active_class="active",
    progress=".rate",
    course_title=".course-name",
)

LEGACY = Catalog(
    key="legacy",
    label="翻转课（旧版）",
    item=".clearfix.video",
    active=".clearfix.video.current_play",
    finish=".time_icofinish",
    title="#lessonOrder",
    active_class="current_play",
    progress=".progress-num",
    course_title=".source-name",
)

ALL_CATALOGS = (WISDOM, FUSION, HIKE, LEGACY)


def catalog_candidates(url: str) -> tuple[Catalog, ...]:
    """按 URL 猜测最可能的目录类型，命中率从高到低。"""
    url = url or ""
    if "hike.zhihuishu.com" in url:
        return (HIKE,)
    if "fusioncourseh5" in url:
        return (FUSION, WISDOM, LEGACY)
    if "studywisdomh5" in url:
        return (WISDOM, FUSION, LEGACY)
    return (WISDOM, LEGACY, FUSION, HIKE)


# ---------------------------------------------------------------------------
# 弹窗 / 遮罩
# ---------------------------------------------------------------------------
# 课中随堂练习（旧版）：弹窗里有题目编号和选项
QUIZ_IN_VIDEO_ROOT = ".el-scrollbar__view"
QUIZ_IN_VIDEO_ALT_ROOT = ".el-dialog"
QUIZ_NUMBER = ".number"
QUIZ_OPTION = ".topic-item"
QUIZ_ANSWERED = ".answer"

# 融合课「AI 随堂练习」：没有关闭按钮，选一个选项再提交即可
AI_EXERCISE_DIALOG = ".ai-class-exercise-dialog"
AI_EXERCISE_OPTION = ".ai-class-exercise-dialog .option"
AI_EXERCISE_SUBMIT = ".ai-class-exercise-dialog .el-dialog__footer button.el-button"
AI_EXERCISE_QUESTION = ".ai-class-exercise-dialog .question-title"

# 新版共享课的「AI 助教提示」弹窗：show-close=false、点遮罩/ Esc 都关不掉，
# 只有右侧那个 .btn 能关（关闭按钮文案来自 i18n 的 argsBtn08）
AI_NOTICE_DIALOG = ".ai-notice-dialog"
AI_NOTICE_CLOSE = (
    ".ai-notice-dialog .ai-notice-wrapper-right .btn, "
    ".ai-notice-dialog .ai-notice-wrapper .btn"
)

# 新版共享课的「AI 随堂练习」弹窗：未作答不能关闭（i18n: 未作答的弹题不能关闭），
# 提交按钮文案是「提交作答」，选项结构随版本变化，所以用一组候选选择器兜住。
AI_QUIZ_OPTION_SELECTORS = (
    ".quest-item .option",
    ".question-item .option",
    ".option-item",
    ".quest-option",
    ".el-radio-wrapper",
    "[role='radio']",
    ".el-checkbox-wrapper",
    "[role='checkbox']",
)

# 从 index-7NdklYGJ.js 编译模板里读到的真实结构（新版共享课 AI 随堂练习）：
# 新版共享课「AI 随堂练习」弹窗 —— 实测 DOM（用户从浏览器复制出来的）：
#
#   div.ai-test-question-wrapper          <- 弹窗根。注意它既不是 el-dialog 也不是 el-overlay！
#     div.header-box
#       p.tit「AI随堂练习」 + div.close-box > svg.icon-close   <- 关闭事件绑在 svg 上
#     div.question-body > .out-contioner > .el-scrollbar > ... > div.ques
#       div.item.ques-card-box            <- 一道题
#         div.row > span.type + span.question
#         div.options > div.option        <- 点击选中
#                        span.class-question-select  (A/B/C/D，选中时加 isSelect)
#                        span.answer      <- 选项正文
#         div.analyze.ques-card-box       <- 作答后出现的解析
#     div.submit-footer > div.submit-btn > span.submits「提交作答」 <- 点击事件绑在 span 上
#                                        > span.done「已提交」     <- 提交后替换
#
# 两个坑（都踩过）：
#   1. 点外层 .submit-btn / .close-box 不会触发事件，事件绑在里面的 span / svg 上；
#   2. 未作答时点关闭会被拦下并提示「未作答的弹题不能关闭」，所以必须先提交再关。
AI_QUIZ_PANEL = ".ai-test-question-wrapper"
AI_QUIZ_OPTION = ".ai-test-question-wrapper .ques .option"
AI_QUIZ_SUBMIT = ".ai-test-question-wrapper .submits"
AI_QUIZ_DONE = ".ai-test-question-wrapper .done"
AI_QUIZ_CLOSE = ".ai-test-question-wrapper .close-box .icon-close"

QUIZ_OPTION_SELECTORS = (
    AI_QUIZ_OPTION,
    ".ques .options .option",
    ".options .option",
    ".option",
    ".option-item",
    ".topic-item",
    ".answer-item",
    ".choice-item",
    ".subject-option",
    ".quest-option",
    ".question-item .option",
    ".el-radio-wrapper",
    "[role='radio']",
    ".el-checkbox-wrapper",
    "[role='checkbox']",
    ".optionList li",
    "li.option",
)
QUIZ_SUBMIT_SELECTORS = (
    ".submits",
    ".submit-btn",
    "button",
    ".el-button",
    ".btn",
    "[class*='submit']",
    "[class*='Submit']",
)
# 提交按钮文案，从最具体到最宽松排列
QUIZ_SUBMIT_TEXTS = (
    "提交作答", "提交答案", "提交", "确定", "确认", "下一题", "继续", "完成", "知道了",
)
QUIZ_CLOSE_SELECTORS = (
    ".close-box .icon-close",
    ".close-box svg",
    ".close-box",
    ".el-dialog__headerbtn",
    ".el-message-box__headerbtn",
    "[class*='dialog-close']",
    "[class*='close-btn']",
    ".iconfont.iconguanbi",
)
QUIZ_GROUP_SELECTORS = (
    ".ques-card-box",
    ".item",
    ".question",
    ".question-item",
    ".exam-item",
    ".subject",
    ".topic",
    "[class*='question']",
    "[class*='subject']",
    "[class*='topic']",
    "li",
)

# 出现这些词才算题目弹窗，避免把公告、提示当成题目
QUIZ_HINTS = (
    "随堂练习", "章节练习", "章节测试", "答题", "测验", "题目", "作答",
)

# 只有带这些字样的才是「章节测验」这类计分内容。
# 注意：随堂练习面板（.ai-test-question-wrapper）**不算**，
# 它每个视频都有、不计分，和章节测验完全是两回事。
CHAPTER_TEST_HINTS = (
    "章节练习", "章节测试", "章节测验", "单元测试", "章节作业", "章测试",
)

# 会挡住视频、必须处理掉的遮罩（Element Plus 的模态遮罩层）
MODAL_OVERLAY_SELECTORS = (
    AI_QUIZ_PANEL,   # 随堂练习面板一出现，站点就会把视频按停
    ".el-overlay",
    ".v-modal",
)

# 会挡住视频、必须处理的遮罩（出题时视频应当暂停）
TOPIC_TITLE = ".topic-title"
BLOCKING_OVERLAYS = (TOPIC_TITLE, ".ss2077-custom-dialog")

# 「章节测验」在课程目录里的条目（来自前端 bundle 的类名：
# item-test / item-test-left-name / item-test-right / item-test-right-icon）
CHAPTER_ITEM_SELECTORS = (
    ".catalogue .item-test",
    ".category-wrapper .item-test",
    ".item-test",
)
CHAPTER_ITEM_TITLE_SELECTORS = (
    ".item-test-left-name",
    ".itme-test-left",       # 站点自己的拼写错误，保留
    ".item-test-left span",
    ".item-test-left",
    ".name-box",
    ".child-name",
    "span[title]",
)
# 这些是按钮文案，不能当成章节测验的名字
CHAPTER_ITEM_TITLE_NOISE = ("去完成", "开始测试", "已完成", "未完成", "开始答题", "已提交")
# 出现这些就算已完成（不确定，先把常见的都覆盖上，日志里会打印实际类名）
CHAPTER_ITEM_DONE_HINTS = ("finish", "finished", "done", "complete", "check", "已")

# 学前必读 / 学习时长提示等公告弹窗的关闭按钮
PREREAD_CLOSE = (
    ".ss2077-custom-modal .ss2077-custom-dialog .ss2077-custom-title > img.icon, "
    ".ss2077-custom-dialog .ss2077-custom-title > img.icon"
)
STUDYTIME_DIV = ".studytime-div"
POPUP_CLOSE_ICON = ".iconfont.iconguanbi"
PATTERN_BTN = ".Patternbtn-div"

# 「网络错误」类提示框里通常是「确定 / 重新加载 / 继续播放」
DIALOG_ROOTS = (
    AI_QUIZ_PANEL,
    ".el-message-box",
    ".el-dialog",
    ".ss2077-custom-dialog",
    ".layui-layer",
    ".dialog-box",
)
DIALOG_CONFIRM_TEXTS = ("确定", "确认", "重新加载", "继续播放", "知道了", "关闭")
DIALOG_DISMISS_TEXTS = ("取消", "稍后")

# 播放器外壳（用于点击视频区域、判断页面是否加载完成）
VIDEO_AREA = ".videoArea, #vjs_container, .video-box, video"

# 播放器调节 UI（改 video 属性后同步界面显示，避免站点自己把值改回去）
SPEED_LABEL = ".speedBox span"
VOLUME_BOX = ".volumeBox"

# ---------------------------------------------------------------------------
# 目录折叠 / AI 目录
# ---------------------------------------------------------------------------
COLLAPSE_ITEM = ".el-collapse-item"
COLLAPSE_HEADER = ".el-collapse-item__header"

# ---------------------------------------------------------------------------
# 章节测验的「整页试卷」（在线考试页，实测 DOM）
#
#   div.myschool_ewcon                                   <- 试卷容器
#     h1.titleLength                                     <- 试卷名
#     button.btnStyleXSumit > span 提交作业
#     button.btnStyleX      > span 暂存作业
#     div.examPaper_box
#       div.examPaper_subject          <- 一道题（25 道都在 DOM 里，只有当前题可见）
#         div.subject_stem
#           div.subject_num > span     <- 题号
#           span.subject_type > span   <- 【单选题】/【多选题】/【判断题】
#           div.subject_describe       <- 题干
#         div.subject_node
#           div.nodeLab                <- 一个选项
#             input[type=radio|checkbox]  (display:none, value=选项id)
#             span.ABCase / span.mr10     <- A. / B. / 对 / 错
#             div.node_detail.examquestions-answer  <- 选项正文
#     div.answerCard                   <- 答题卡（题号跳转）
#
# 注意：题目**一页一题**，需要靠「下一题」或答题卡翻页。
# 另外站点把「是否已作答」放在隐藏 input 的 :checked 上，
# 选项里的 img.flagChecked 每个选项都有，绝不能拿它判断选中。
# ---------------------------------------------------------------------------
EXAM_ROOT = ".examPaper_box"
EXAM_QUESTION = ".examPaper_subject"
EXAM_TYPE = ".subject_type"
EXAM_STEM = ".subject_describe"
EXAM_STEM_FALLBACK = ".subject_stem"
EXAM_OPTION = ".subject_node .nodeLab"
EXAM_OPTION_TEXT = ".node_detail"
EXAM_OPTION_LETTER = ".ABCase"
EXAM_SUBMIT_TEXT = ("提交作业", "提交试卷", "交卷", "提交")
# 注意：最后一题时「下一题」按钮会变成「保存」，点它才会保存全部答案，
# 而且**不会关闭标签页**；「暂存作业」会弹确认框、确认后还会关掉标签页，别用。
EXAM_SAVE_TEXT = ("保存",)
EXAM_DRAFT_TEXT = ("暂存作业", "暂存")
EXAM_NEXT_TEXT = ("下一题", "下题")
EXAM_PREV_TEXT = ("上一题", "上题")
EXAM_CONFIRM_TEXT = ("确定", "确认", "提交")

# 答题卡：完成率 + 每题的作答状态
EXAM_CARD_ITEM = ".answerCard_list li"
EXAM_CARD_RATE = ".percentage_tit em i, .percentage_tit em"
# 答题卡题号上表示"已作答"的类名（实测已答的是 green）
EXAM_CARD_DONE_HINTS = ("green", "done", "finish", "complete", "checked")

# ---------------------------------------------------------------------------
# 课程目录的展开/折叠
#
# 实测：随堂练习弹出时站点会把章节目录收起来
#   <div class="wisdom-category-box hide-box animate-css">     <- hide-box = 隐藏
#   <div class="side-expand-box animated-box show">            <- 点它展开
#     <svg class="svg-icon collapse-btn">…</svg>
# 目录被收起后程序找不到目录项，就会卡住；所以每次要用目录前都要确认它是展开的。
# ---------------------------------------------------------------------------
CATALOG_BOX = ".wisdom-category-box, .catalogue, .category-wrapper"
CATALOG_HIDDEN_CLASS = "hide-box"
CATALOG_EXPAND_BUTTON = (
    ".side-expand-box",
    ".side-expand-box .collapse-btn",
    ".collapse-btn",
)
