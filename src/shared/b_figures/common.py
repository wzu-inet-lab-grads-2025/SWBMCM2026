"""B 题各问归档图集共用的版式、场地图和输入追溯工具。
输入为分问归档及 Figure，输出为 PDF/PNG（Q1 另有 SVG）与逐图输入的 SHA-256。
由 src.qN.archive_plots 调用；运行 nohup python3 -B -m src.shared.b_figures q1。
"""

import csv
import gzip
from hashlib import file_digest
import json
import math
from pathlib import Path
import platform

import matplotlib as mpl
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Patch, Rectangle
import numpy as np


ROOT = Path(__file__).resolve().parents[3]
ARCHIVES = {
    "q1": Path("results/q1/可观测区域与最优站位_连续圆弧_20261120"),
    "q2": Path("results/q2/双站定位与并发调度_连续站位_20261120"),
    "q3": Path("results/q3/测向可靠性与80米定位约束_五方法对照_20261120"),
    "q4": Path("results/q4/扰动重调度_四模型择优_20261120"),
}

# 图页留白，图内按用途用淡底；蓝色突出主结果，橙色点出对照、边界或扰动。
STYLE_NAME = "paper-blue-orange"
INK = "#202831"
MUTED = "#4d5965"
SECONDARY = "#65717d"
RULE = "#a1aab3"
GRID = "#e9edf1"
PLOT_BG = "#fafbfc"
FIELD_BG = "#faf8f4"
BLUE = "#065cc8"
BLUE_TINT = "#e3eefb"
MASK_TINT = "#d5e6f8"  # 面积填色比细条更需要在暖灰底和灰度稿上分得开。
ORANGE = "#bf480c"
ORANGE_TINT = "#fae8d8"
BUILDING = "#eaecf0"

# 行驶靠冷灰、等待靠浅底和轮廓；正反斜线区分指向和复位，灰度下也可读。
PHASE_STYLES = {
    "TRAVELING": {"facecolor": "#becfe0", "edgecolor": "#768da3", "linewidth": .45},
    "SLEWING": {"facecolor": "#f4ede4", "edgecolor": "#9b8a75", "linewidth": .4, "hatch": "//"},
    "READY_WAITING": {"facecolor": "#f6f7f9", "edgecolor": "#a1aab3", "linewidth": .5},
    "OBSERVING": {"facecolor": BLUE_TINT, "edgecolor": "#7f9ebf", "linewidth": .5},
    "RESETTING": {"facecolor": "#e9e9ef", "edgecolor": "#8c899b", "linewidth": .4, "hatch": "\\\\"},
    "CANCEL_RESET": {"facecolor": ORANGE_TINT, "edgecolor": "#a77c60", "linewidth": .45, "hatch": "+"},
    "IDLE": {"facecolor": "#f5f6f8", "edgecolor": "none", "linewidth": 0},
    "FAILED": {"facecolor": "#e7e9ec", "edgecolor": "#9aa3ad", "linewidth": .45, "hatch": "x"},
}
EMPHASIZED_PHASES = {
    "TRAVELING": {"facecolor": PHASE_STYLES["TRAVELING"]["facecolor"], "edgecolor": BLUE, "linewidth": 1.25},
    "OBSERVING": {"facecolor": BLUE, "edgecolor": BLUE, "linewidth": .5},
}
PHASE_LABELS = {"TRAVELING": "Travel", "SLEWING": "Slew", "READY_WAITING": "Wait for partner",
                "OBSERVING": "Observe", "RESETTING": "Reset", "CANCEL_RESET": "Cancel / reset",
                "IDLE": "Idle", "FAILED": "Withdrawn"}

STYLE = {
    "font.family": "DejaVu Sans", "font.size": 10.5, "text.color": INK,
    "axes.labelcolor": INK, "axes.labelsize": 10.5, "axes.edgecolor": RULE,
    "axes.titleweight": "semibold", "axes.titlesize": 11, "axes.titlepad": 8,
    "axes.linewidth": .7, "figure.facecolor": "white", "savefig.facecolor": "white",
    "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelsize": 9.5, "ytick.labelsize": 9.5,
    "xtick.major.size": 3, "ytick.major.size": 3, "legend.fontsize": 9.2, "legend.frameon": False,
    "grid.color": GRID, "grid.linewidth": .65, "hatch.linewidth": .4, "pdf.fonttype": 42,
}


def read_json(path: Path):
    """读取 UTF-8 JSON 归档。"""
    return json.loads(path.read_text(encoding="utf-8"))


def records(path: Path) -> list[dict]:
    """原样读取 UTF-8 CSV 字段，类型转换由各图按列执行。"""
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def json_lines(path: Path):
    """逐行解码压缩 JSONL；无需把完整车辆轨迹加载到内存。"""
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        yield from map(json.loads, stream)


def new_figure(size, title, subtitle) -> Figure:
    """为结论标题和统计口径预留页眉，子图不承担说明性段落。"""
    fig = Figure(figsize=size)
    FigureCanvasAgg(fig)
    fig.text(.045, .962, title, fontsize=16, weight="semibold", va="top")
    fig.text(.045, .962 - .36 / size[1], subtitle, fontsize=10, color=MUTED, va="top")
    return fig


def footnote(fig, text) -> None:
    """在图内单设图注区；显式换行，确保 PDF 与 PNG 边距相同。"""
    fig.text(.045, .028, text, fontsize=9, color=MUTED, va="bottom", linespacing=1.35)


def clean_axes(ax, *, grid="y") -> None:
    """定量面板用浅冷灰底、两根轴和低对比度参考网线。"""
    ax.set_facecolor(PLOT_BG)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_axisbelow(True)
    ax.grid(axis=grid)


def field_axes(ax, cfg, *, extent=None, ticks=None) -> None:
    """用浅暖底与等比例米制坐标绘制场界；局部图只在可见楼面标楼号。"""
    x0, x1, y0, y1 = extent or (-1.6, cfg.field_width + 1.6, -1.6, cfg.field_height + 1.6)
    ax.set_facecolor(FIELD_BG)
    ax.add_patch(Rectangle((0, 0), cfg.field_width, cfg.field_height, fc="none", ec=RULE, lw=.9, zorder=1))
    for b in cfg.buildings:
        ax.add_patch(Rectangle((b.x_min, b.y_min), b.x_max - b.x_min, b.y_max - b.y_min,
                               fc=BUILDING, ec=RULE, lw=.65, zorder=2))
        left, right = max(x0, b.x_min), min(x1, b.x_max)
        low, high = max(y0, b.y_min), min(y1, b.y_max)
        if right - left >= 2 and high - low >= 2:
            ax.text((left + right) / 2, (low + high) / 2, b.id, ha="center", va="center",
                    fontsize=8.8, color=MUTED, zorder=3)
    ax.set(xlim=(x0, x1), ylim=(y0, y1), aspect="equal", xlabel="East (m)", ylabel="North (m)")
    if ticks is None:
        ticks = (np.arange(0, cfg.field_width + 1, 20), np.arange(0, cfg.field_height + 1, 20))
    ax.set_xticks(ticks[0])
    ax.set_yticks(ticks[1])


def route_arrow(ax, points, color, *, minimum_m: float = 0) -> None:
    """原折线路点不平滑；在最长线段上标行驶方向，避免短段箭头挡住站位。"""
    if len(points) < 2:
        return
    a, b = map(np.asarray, max(zip(points, points[1:]), key=lambda pair: math.dist(*pair)))
    if math.dist(a, b) <= minimum_m:
        return
    ax.annotate("", xy=a + .70 * (b - a), xytext=a + .36 * (b - a), zorder=5,
                arrowprops={"arrowstyle": "-|>", "color": color, "lw": 1.5, "mutation_scale": 12,
                            "shrinkA": 0, "shrinkB": 0})


def phase_label(ax, time_s, vehicle_index, label, kind, *, emphasized=False) -> None:
    """相位标签沿用高对比文字；只有蓝色实填的重点观测使用白字。"""
    color = ("white" if kind == "OBSERVING" else BLUE) if emphasized else INK
    ax.text(time_s, vehicle_index, label, ha="center", va="center", color=color,
            fontsize=9, weight="semibold", clip_on=True, zorder=5)


def phase_bar(ax, vehicle_index, start, end, kind, *, label=None, emphasized=False) -> None:
    """从连续端点（s）画区间；仅突出关键行驶/观测，不把零时长事件画成条。"""
    if end <= start:
        return
    style = EMPHASIZED_PHASES[kind] if emphasized else PHASE_STYLES[kind]
    ax.barh(vehicle_index, end - start, left=start, height=.61, **style, zorder=2)
    if label:
        phase_label(ax, (start + end) / 2, vehicle_index, label, kind, emphasized=emphasized)


def phase_keys(kinds) -> list[Patch]:
    """图例沿用相位条的底色和纹理；重点观测配独立色样，但不算另一相位。"""
    keys = []
    for kind, style in PHASE_STYLES.items():
        if kind not in kinds:
            continue
        keys.append(Patch(**style, label=PHASE_LABELS[kind]))
        if kind == "OBSERVING":
            keys.append(Patch(**EMPHASIZED_PHASES[kind], label="Key observation"))
    return keys


def digest(path: Path) -> str:
    """按文件原始字节计算 SHA-256（压缩底表用存档字节）。"""
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


class Atlas:
    """在独立目录发布图件并收集溯源；路径存为仓库相对路径。"""

    def __init__(self, source: Path, output: Path):
        """source 为只读正式归档，output 为 CLI 本次生成的目录。"""
        self.source = source
        self.output = output
        self.entries = {}
        self._hashes = {}

    def _digests(self, paths) -> dict:
        """同一份较大的逐场文件只读一遍用于求哈希。"""
        result = {}
        for path in paths:
            relative = path.resolve().relative_to(ROOT).as_posix()
            if relative not in self._hashes:
                self._hashes[relative] = digest(path)
            result[relative] = self._hashes[relative]
        return result

    def export(self, fig: Figure, stem: str, inputs, transform: str, *, svg: bool = False) -> None:
        """按同一画布保存 220 dpi PNG、PDF 和可选的 SVG，记录显示变换与文件哈希。"""
        source = self._digests(inputs)
        pdf, png = (self.output / f"{stem}.{suffix}" for suffix in ("pdf", "png"))
        fig.savefig(pdf, dpi=220, metadata={"CreationDate": None, "ModDate": None})
        fig.savefig(png, dpi=220)
        outputs = [pdf, png]
        if svg:
            svg_path = self.output / f"{stem}.svg"
            # 字形转路径以便跨机器排版；固定元素 ID 和去掉日期，便于按哈希复核。
            with mpl.rc_context({"svg.fonttype": "path", "svg.hashsalt": stem}):
                fig.savefig(svg_path, metadata={"Date": None})
            outputs.append(svg_path)
        self.entries[stem] = {"inputs": source, "visual_transform": transform,
                              "outputs": {out.name: digest(out) for out in outputs}}
        fig.clear()

    def write_provenance(self, question, command, head, generators, *, visual_style: str = STYLE_NAME) -> None:
        """记录默认复跑命令、环境、工作树脚本哈希及对应的正式归档。"""
        data = {"question": question, "visual_style": visual_style, "source_archive": self.source.relative_to(ROOT).as_posix(),
                "replay_command": command, "git_head": head, "generator_files": self._digests((*generators, Path(__file__))),
                "python": platform.python_version(), "matplotlib": mpl.__version__, "numpy": np.__version__,
                "figures": self.entries}
        (self.output / "provenance.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                                     encoding="utf-8")
