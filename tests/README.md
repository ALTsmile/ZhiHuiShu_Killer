# 自检脚本

这些脚本是**离线自检**：用假的页面 DOM 跑完整流程，
不需要登录、不联网、不会碰你的账号，改完代码跑一遍就知道有没有弄坏东西。

在项目根目录执行（需要本机装了 Chrome 或 Edge）：

```bash
python tests/offline_check.py     # 主自检：随堂练习、章节测验、滑块、日志清理、AI 兜底…
python tests/offline_login.py     # 登录状态判定与登录流程
python tests/offline_ui.py        # 界面弹窗、消息泵、异常落盘
python tests/check_ui_layout.py   # 量各页签需要多高、日志区占比（只打印，不判定）
python tests/offline_runner_order.py  # 仅答题模式参数初始化与快速通道分支
python tests/offline_exam_input.py    # 新版试卷完成率、真实鼠标点击、开考跳转
```

带断言的脚本末尾会打印 `全部通过 ✅`，退出码 0 表示没问题。

`fixtures/` 里是自检用的离线页面样本（比如"整页试卷"的 DOM 结构），
全部是手工构造的假数据，**不含任何真实账号、课程或成绩信息**。

> 发布用的 zip 包里**不包含** `tests/`，普通用户不需要这些脚本。
