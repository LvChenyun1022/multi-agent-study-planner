# -*- coding: utf-8 -*-
"""生成设计书图1（多智能体协同架构）与图2（动态重规划流程）为 PNG（v2 布局修正）。"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mp
from matplotlib import font_manager

# 输出目录：脚本所在目录下的 assets/（可配置，不写死绝对路径）
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
os.makedirs(OUT_DIR, exist_ok=True)

def pick_cjk():
    names = {f.name for f in font_manager.fontManager.ttflist}
    for cand in ["Noto Sans CJK SC", "Noto Sans CJK", "Source Han Sans SC",
                 "Microsoft YaHei", "SimHei", "SimSun", "WenQuanYi Zen Hei"]:
        if cand in names:
            return cand
    return None

cjk = pick_cjk()
if cjk:
    plt.rcParams["font.family"] = cjk
plt.rcParams["axes.unicode_minus"] = False

DARK, PRIMARY, FILL, ACCENT, WARM, WARM_EC = "#263238", "#37474F", "#EEF3F6", "#1F6FB2", "#FDF3E1", "#C9A227"

def box(ax, x, y, w, h, text, fc=FILL, fs=11, bold=False, ec=PRIMARY):
    ax.add_patch(mp.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                   fc=fc, ec=ec, lw=1.4))
    ax.text(x + w/2, y + h/2, text, ha="center", va="center", fontsize=fs,
            color=DARK, fontweight="bold" if bold else "normal", linespacing=1.5)

def arrow(ax, x1, y1, x2, y2, dashed=False):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=PRIMARY, lw=1.6,
                                linestyle=(0, (4, 3)) if dashed else "solid"))

def elbow(ax, pts, dashed=False, label="", label_pos=None):
    """折线连接：pts 为拐点列表，最后一段带箭头。"""
    for i in range(len(pts) - 2):
        ax.plot([pts[i][0], pts[i+1][0]], [pts[i][1], pts[i+1][1]],
                color=PRIMARY, lw=1.6, linestyle=(0, (4, 3)) if dashed else "solid")
    (x1, y1), (x2, y2) = pts[-2], pts[-1]
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=PRIMARY, lw=1.6,
                                linestyle=(0, (4, 3)) if dashed else "solid"))
    if label and label_pos:
        ax.text(*label_pos, label, ha="center", va="center", fontsize=9.5, color=ACCENT)

# ============ 图 1：多智能体协同架构 ============
fig, ax = plt.subplots(figsize=(11.5, 8.4), dpi=180)
ax.set_xlim(0, 100); ax.set_ylim(0, 74); ax.axis("off")

box(ax, 32, 66, 36, 6, "学生与考试信息", fs=12, bold=True)
box(ax, 24, 52, 52, 9, "Orchestrator 调度中心\nDAG 调度 · 状态机 · CRISP 上下文筛选 · 失败重试",
    fs=12, bold=True, fc="#DCE7F0")
arrow(ax, 50, 66, 50, 61.6)

agents = [("01 考试分析\nExam Analyst", 2), ("02 知识诊断\nKnowledge\nDiagnostician", 21.5),
          ("03 复习决策\nStrategic\nPlanner", 41), ("04 自适应辅导\nAdaptive\nTutor", 60.5),
          ("05 进度监控\nProgress\nMonitor", 80)]
for name, x in agents:
    box(ax, x, 33, 18, 10, name, fs=10.5, bold=True)
    arrow(ax, 50, 52, x + 9, 43.6)

# 中间行：Memory / 算法 / 执行
box(ax, 2, 20, 24, 8, "学生状态与长期记忆\n（考试分析/诊断/监控写入）", fs=10)
box(ax, 36, 20, 28, 8, "风险评估与约束优化算法", fc=WARM, ec=WARM_EC, fs=11, bold=True)
box(ax, 74, 20, 24, 8, "复习任务执行（学生）", fs=11)

arrow(ax, 11, 33, 13, 28.6)                 # A1 -> Memory
elbow(ax, [(30.5, 33), (30.5, 30.6), (20, 30.6), (20, 28.6)])   # A2 -> Memory
elbow(ax, [(86, 33), (86, 30.6), (24, 30.6), (24, 28.6)], dashed=True)  # A5 -> Memory
arrow(ax, 50, 33, 50, 28.6)                 # A3 -> 算法
arrow(ax, 71, 33, 80, 28.6)                 # A4 -> 执行
arrow(ax, 88, 28.6, 88, 33)                 # 执行 -> A5

# A5 -> Orchestrator：触发重规划（右侧绕行）
elbow(ax, [(89, 43.6), (99, 47), (99, 56.5), (76.6, 56.5)], dashed=True,
      label="触发重规划", label_pos=(92, 60))

# 底部基础设施
box(ax, 2, 4, 96, 10, "共享基础设施\nMCP 工具层（课程资料 / 考试安排 / 日历 / 错题记录 / 任务读写）\n"
                      "Harness 权限控制与人工审批 · LLM Client（致远一号本地 API + MockLLM 兜底）",
    fs=10.5, fc=FILL)
elbow(ax, [(30.5, 20), (30.5, 14.6)], dashed=True)   # agent -> infra（左）
elbow(ax, [(70, 20), (70, 14.6)], dashed=True)       # agent -> infra（右）
ax.text(50, 17.2, "统一 call_tool（敏感操作先过 Harness 审批）", ha="center",
        fontsize=9.5, color=ACCENT)

ax.set_title("图 1  ExamOS 多智能体协同架构", fontsize=15, color=DARK, fontweight="bold", pad=12)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "fig1_architecture.png"), bbox_inches="tight")
plt.close(fig)

# ============ 图 2：动态重规划机制 ============
fig, ax = plt.subplots(figsize=(12.5, 5.8), dpi=180)
ax.set_xlim(0, 110); ax.set_ylim(0, 50); ax.axis("off")

box(ax, 2, 28, 16, 8, "生成计划 v1", fs=11.5, bold=True)
box(ax, 24, 28, 18, 8, "学生执行与系统监测\n记录进度与测验反馈", fs=10.5)
box(ax, 48, 27, 15, 10, "显著偏差且\n过冷却期？", fc=WARM, ec=WARM_EC, fs=11, bold=True)
box(ax, 70, 36, 21, 8, "更新任务状态、剩余\n工作量与剩余可用时间", fs=10)
box(ax, 70, 12, 21, 8, "重算风险评分\n与时间分配", fs=10.5)
box(ax, 95, 28, 14, 8, "生成计划 v2\n附变化解释与剩余风险", fs=9.5, bold=True)
box(ax, 24, 4, 18, 7, "修改约束与偏好", fs=10.5)

arrow(ax, 18, 32, 24, 32)
arrow(ax, 42, 32, 48, 32)
arrow(ax, 63, 34.5, 70, 39)                  # 是
ax.text(65.5, 39.5, "是", fontsize=10, color=ACCENT)
arrow(ax, 80.5, 36, 80.5, 20.6)              # 更新 -> 重算
arrow(ax, 91, 16, 98, 28)                    # 重算 -> v2
arrow(ax, 102, 36.6, 102, 44)
ax.text(102, 47, "学生确认后生效，进入下一轮执行", ha="center", fontsize=10, color=ACCENT)

# 否：返回继续执行（弧线）
ax.annotate("", xy=(33, 27.6), xytext=(52, 26.6),
            arrowprops=dict(arrowstyle="-|>", color=PRIMARY, lw=1.6,
                            connectionstyle="arc3,rad=0.25"))
ax.text(40, 20.5, "否：继续执行", ha="center", fontsize=9.5, color=ACCENT)

# 拒绝/调整：v2 底部 → 向下 → 修改约束与偏好（底部折线绕行，不穿越重算框）
elbow(ax, [(99, 28), (99, 7.5), (42.6, 7.5)], dashed=True,
      label="拒绝或调整", label_pos=(68, 4.6))
# 修改约束与偏好 -> 重算
elbow(ax, [(42, 9.5), (60, 9.5), (60, 16), (69.4, 16)], dashed=True)

ax.set_title("图 2  动态重规划机制", fontsize=15, color=DARK, fontweight="bold", pad=12)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "fig2_replanning.png"), bbox_inches="tight")
plt.close(fig)
print("OK", cjk)