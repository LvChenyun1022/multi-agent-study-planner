# -*- coding: utf-8 -*-
"""ExamOS 初赛演示视频生成器：用基准输出渲染终端录屏风格 1080p 视频。
内容 = chusai-demo-baseline 的真实输出（demo_output.txt）+ 22 项测试真实结果。
"""
import subprocess, unicodedata, math, os
from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg

W, H, FPS = 1920, 1080, 24
BASE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(BASE, "examos-2.0", "demo_output.txt")
OUT = os.path.join(BASE, "ExamOS_初赛演示视频_v1.mp4")

CONS = "C:/Windows/Fonts/consola.ttf"
MSYH = "C:/Windows/Fonts/msyh.ttc"
MSYHB = "C:/Windows/Fonts/msyhbd.ttc"

FS = 26                      # 终端字号
f_cons = ImageFont.truetype(CONS, FS)
f_cjk = ImageFont.truetype(MSYH, FS)
f_cjk_b = ImageFont.truetype(MSYHB, FS)
CELL_W = f_cons.getlength("0")
CELL_H = 34
TERM_X, TERM_Y = 60, 96      # 终端文本区左上角
TERM_W = W - 2 * TERM_X

BG = (13, 17, 23); FG = (201, 209, 217); DIM = (139, 148, 158)
ACCENT = (88, 166, 255); GOOD = (63, 185, 80); WARN = (210, 153, 34)
HL = (31, 111, 235, 78); HLGOOD = (63, 185, 80, 66)
CHROME = (22, 27, 34)

demo_lines = open(DEMO, encoding="utf-8").read().splitlines()

def is_cjk(ch):
    return unicodedata.east_asian_width(ch) in ("W", "F")

def draw_line(dr, x0, y, text, color=FG, bold=False):
    cx = x0
    for ch in text:
        if is_cjk(ch):
            dr.text((cx, y), ch, font=(f_cjk_b if bold else f_cjk), fill=color)
            cx += 2 * CELL_W
        else:
            dr.text((cx, y), ch, font=f_cons, fill=color)
            cx += CELL_W

def line_color(t):
    s = t.strip()
    if not s: return DIM
    if set(s) == {"="}: return DIM
    if s.startswith(("一、", "二、", "三、", "四、", "五、")): return ACCENT
    if s.startswith("——"): return ACCENT
    if "守恒校验" in s and "通过" in s: return GOOD
    if s.startswith("*") or s.startswith("---"): return WARN
    if "风险评分" in s or s.startswith("重规划后"): return ACCENT
    return FG

def build_text_image(lines, prompt=None):
    h = max(len(lines) * CELL_H + 8, VIEW_H)
    img = Image.new("RGB", (TERM_W, h), BG)
    dr = ImageDraw.Draw(img)
    y = 0
    if prompt is not None:
        draw_line(dr, 0, y, prompt, GOOD); y += CELL_H
    for t in lines:
        draw_line(dr, 0, y, t, line_color(t)); y += CELL_H
    return img

def ease(u):
    return u * u * (3 - 2 * u)

def scroll_at(keys, t):
    """keys: [(t, top_line), ...] 线性分段 + 平滑"""
    if t <= keys[0][0]: return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * ease((t - t0) / (t1 - t0))
    return keys[-1][1]

def highlights_at(hls, t):
    """hls: [(t0, t1, line_idx, col_start|None, col_end|None, good)]"""
    out = []
    for t0, t1, li, c0, c1, good in hls:
        if t0 <= t < t1:
            out.append((li, c0, c1, good))
    return out

VIEW_H = H - TERM_Y - 150     # 视口高度（留字幕条）

def term_frame(text_img, scroll, hls, elapsed, total, sub_lines):
    frame = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(frame, "RGBA")
    # 窗口栏
    dr.rectangle([0, 0, W, 72], fill=CHROME)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        dr.ellipse([28 + i * 30, 28, 44 + i * 30, 44], fill=c)
    dr.text((130, 24), "PS E:\\KimiData\\kimi\\Workspaces\\交我创\\examos-2.0 — python", font=f_cjk, fill=DIM)
    # 文本视口
    max_scroll = max(0, text_img.height - VIEW_H)
    y = int(min(scroll * CELL_H, max_scroll))
    view = text_img.crop((0, y, TERM_W, y + VIEW_H))
    frame.paste(view, (TERM_X, TERM_Y))
    # 高亮（行号基于 text_img 坐标系；prompt 占第 0 行时 hls 已含偏移）
    for li, c0, c1, good in hls:
        ry = TERM_Y + li * CELL_H - y
        if -CELL_H < ry < H:
            x0 = TERM_X + (c0 or 0)
            x1 = TERM_X + (c1 if c1 else TERM_W)
            dr.rectangle([x0, ry, x1, ry + CELL_H],
                         fill=(HLGOOD if good else HL))
    add_subtitle(frame, sub_lines)
    return fade(frame, elapsed, total)

def add_subtitle(frame, lines):
    if not lines: return
    dr = ImageDraw.Draw(frame, "RGBA")
    bar_h = 56 + 46 * (len(lines) - 1)
    y0 = H - bar_h - 28
    dr.rectangle([0, y0, W, H], fill=(0, 0, 0, 185))
    f_sub = ImageFont.truetype(MSYHB, 32)
    ty = y0 + 22
    for ln in lines:
        w = dr.textlength(ln, font=f_sub)
        dr.text(((W - w) / 2, ty), ln, font=f_sub, fill=(245, 245, 245))
        ty += 46

def fade(frame, elapsed, total, d=0.45):
    k = min(elapsed / d, 1.0, (total - elapsed) / d)
    if k >= 1.0: return frame
    return Image.blend(Image.new("RGB", (W, H), (0, 0, 0)), frame, max(k, 0.0))

def sub_at(schedule, t):
    for t0, t1, lines in schedule:
        if t0 <= t < t1: return lines
    return None

def card_frame(lines_big, lines_small, sub_lines, elapsed, total, footer=None):
    frame = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(frame, "RGBA")
    dr.rectangle([0, 0, W, 6], fill=ACCENT)
    f_title = ImageFont.truetype(MSYHB, 96)
    f_body = ImageFont.truetype(MSYHB, 40)
    f_small = ImageFont.truetype(MSYH, 28)
    y = 250
    for txt, color in lines_big:
        w = dr.textlength(txt, font=f_title)
        dr.text(((W - w) / 2, y), txt, font=f_title, fill=color); y += 128
    for txt in lines_small:
        w = dr.textlength(txt, font=f_body)
        dr.text(((W - w) / 2, y + 16), txt, font=f_body, fill=FG); y += 64
    if footer:
        w = dr.textlength(footer, font=f_small)
        dr.text((W - w - 40, 28), footer, font=f_small, fill=DIM)
    add_subtitle(frame, sub_lines)
    return fade(frame, elapsed, total)

# ---------------- 场景定义 ----------------
P = "PS E:\\KimiData\\kimi\\Workspaces\\交我创\\examos-2.0> "

sec1 = demo_lines[0:8]            # 一、风险评分
sec2 = demo_lines[8:59]           # 二、v1 + 预警
sec3 = demo_lines[59:73]          # 三、执行反馈
sec4 = demo_lines[73:]            # 风险变化 + 四、v2 + 五、差异报告

def find(lines, key, start=0):
    for i in range(start, len(lines)):
        if key in lines[i]: return i
    raise ValueError(key)

# 场景内行号均 +1（prompt 占第 0 行）
scenes = []

# 1 标题卡 15s
scenes.append(dict(kind="card", dur=15.0,
    big=[("ExamOS", ACCENT)], small=["高校多课程考试智能决策与自适应复习系统",
        "「交我创」智能体创新大赛 · 自由选题赛道"],
    subs=[(1.0, 7.0, ["考试周：多门课、时间少、计划总被打乱。"]),
          (7.5, 14.0, ["ExamOS 用多智能体 + 优化算法，做“会调整的复习计划”。"])]))

# 2 风险评分 25s（列扫过高亮 + 高数行）
hdr = next(i for i, l in enumerate(sec1) if "课程" in l and " U " in l)
row_math = find(sec1, "高等数学") + 1
head_line = sec1[hdr]
def cell_off(s, idx):
    return sum(2 if is_cjk(ch) else 1 for ch in s[:idx])
col_x = {}
for name in ["D", "M", "W", "U", "R"]:
    idx = head_line.index(name, 4)
    col_x[name] = int(cell_off(head_line, idx) * CELL_W) - 4
colw = int(4 * CELL_W)
hl2 = []
t0 = 3.0
for name in ["D", "M", "W", "U", "R"]:
    hl2.append((t0, t0 + 2.0, row_math, col_x[name], col_x[name] + colw, False))
    t0 += 2.0
hl2.append((13.0, 24.5, row_math, None, None, False))
scenes.append(dict(kind="term", dur=25.0, lines=sec1, prompt=P + "python scheduler.py",
    keys=[(0, 0), (24.5, 0)], hls=hl2,
    subs=[(1.0, 12.8, ["每门课的风险不由大模型拍脑袋，而是四个可复算的因子：",
                      "紧迫度 D、掌握缺口 M、重要度 W、诊断不确定性 U。"]),
          (13.5, 23.5, ["高数明天考、掌握度低：R=0.675，全场最高风险。"])]))

# 3 v1 30s：滚动 → 预警块停留
warn1 = find(sec2, "偏导数专题", find(sec2, "预警")) + 1
scenes.append(dict(kind="term", dur=30.0, lines=sec2, prompt=P + "python scheduler.py",
    keys=[(0, 0), (3, 0), (16, 28), (19, warn1 - 12), (29.5, warn1 - 12)],
    hls=[(19.5, 29.5, warn1, None, None, False),
         (19.5, 29.5, warn1 + 1, None, None, False),
         (19.5, 29.5, warn1 + 2, None, None, False)],
    subs=[(1.0, 7.0, ["v1：贪心算法按边际收益分配每天的时间块。"]),
          (8.0, 17.0, ["高数明天就考，6 小时排不下全部必修——",
                       "系统不报“无解”，而是出计划 + 写明剩余风险："]),
          (19.5, 28.0, ["哪些知识点被迫放弃，失分敞口一目了然。"])]))

# 4 执行反馈 25s
fb_line = find(sec3, "回补剩余工作量 1.5h") + 1
cons_line = find(sec3, "守恒校验") + 1
scenes.append(dict(kind="term", dur=25.0, lines=sec3, prompt=P + "python scheduler.py",
    keys=[(0, 0), (24.5, 0)],
    hls=[(5.0, 11.0, fb_line, None, None, False),
         (12.0, 24.5, cons_line, None, None, True)],
    subs=[(1.0, 6.0, ["计划赶不上变化：线代当天只完成了 25%。"]),
          (6.0, 12.0, ["未完成的 1.5 小时自动回补剩余工作量；"]),
          (12.5, 23.5, ["掌握度 M 不变——没复习不等于更不会，",
                        "掌握度只按分级证据更新，不污染诊断（设计书 7.1）。"])]))

# 5 v2 + 差异报告 30s
chg = find(sec4, "重规划后风险评分变化")
mtx = find(sec4, "矩阵运算强化") + 1
d1_en = find(sec4, "Day 1 大学英语") + 1
d1_la = find(sec4, "Day 1 线性代数") + 1
scenes.append(dict(kind="term", dur=30.0, lines=sec4, prompt=P + "python scheduler.py",
    keys=[(0, max(0, chg - 4)), (4, max(0, chg - 4)), (10, mtx - 8), (15, mtx - 8),
          (21, d1_en - 12), (29.5, d1_en - 12)],
    hls=[(0.5, 4.0, chg + 2, None, None, False),
         (0.5, 4.0, chg + 3, None, None, False),
         (0.5, 4.0, chg + 4, None, None, False),
         (10.5, 15.0, mtx, None, None, False),
         (10.5, 15.0, mtx + 1, None, None, False),
         (21.5, 24.5, d1_en, None, None, False),
         (24.5, 27.5, d1_la, None, None, False)],
    subs=[(1.0, 6.0, ["检测到显著偏差 → 自动触发重规划："]),
          (6.0, 15.0, ["Day 1 英语 −1.5h，线代 +1.5h——时间真的被重新分配；"]),
          (15.0, 22.0, ["可选任务优先裁剪；必修仍无法全部排入时，",
                        "系统如实列出剩余风险，不伪造完美计划。"]),
          (22.0, 28.0, ["v2 不是重打字，是重新求解。"])]))

# 6a 测试 8s
ut_lines = ["......................",
            "----------------------------------------------------------------------",
            "Ran 22 tests in 0.003s", "", "OK"]
scenes.append(dict(kind="term", dur=8.0, lines=ut_lines,
    prompt=P + "python -m unittest test_scheduler test_planner_scheduler",
    keys=[(0, 0), (7.5, 0)],
    hls=[(4.0, 7.5, 5, None, None, True)],
    subs=[(1.5, 7.0, ["算法可复算、计划可执行、调整可解释、过程可测试。"])]))

# 6b 总结卡 17s
scenes.append(dict(kind="card", dur=17.0,
    big=[("ExamOS", ACCENT)],
    small=["√ 可复算的风险评分与优化分配",
           "√ 时间不足时的降级决策与剩余风险报告",
           "√ 执行反馈驱动的动态重规划",
           "√ 18 项算法边界测试 + 4 项 PlannerAgent 集成测试护航"],
    footer="模拟数据演示，算法行为真实可复现",
    subs=[(2.0, 9.0, ["ExamOS，让考试周的每一小时都花在刀刃上。"])]))

# ---------------- 渲染 ----------------
def main():
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.Popen(
        [ff, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", OUT],
        stdin=subprocess.PIPE)
    total_frames = 0
    for sc in scenes:
        n = int(sc["dur"] * FPS)
        if sc["kind"] == "term":
            text_img = build_text_image(sc["lines"], sc.get("prompt"))
            for f in range(n):
                t = f / FPS
                fr = term_frame(text_img, scroll_at(sc["keys"], t),
                                highlights_at(sc["hls"], t), t, sc["dur"],
                                sub_at(sc["subs"], t))
                proc.stdin.write(fr.tobytes())
        else:
            for f in range(n):
                t = f / FPS
                fr = card_frame(sc["big"], sc["small"], sub_at(sc["subs"], t),
                                t, sc["dur"], sc.get("footer"))
                proc.stdin.write(fr.tobytes())
        total_frames += n
        print(f"scene done ({n} frames)", flush=True)
    proc.stdin.close(); proc.wait()
    dur = total_frames / FPS
    print(f"OK: {OUT}  {dur:.1f}s  {os.path.getsize(OUT)/1e6:.1f} MB")

if __name__ == "__main__":
    main()
