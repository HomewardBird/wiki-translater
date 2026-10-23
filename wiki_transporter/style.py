# -*- coding: utf-8 -*-
"""正文排版的共用规则：说话人配色、演出提示的归类、留白判定。

PDF / Word / EPUB 三种产物都从这里取同一套规则，避免各排各的、三种格式三种观感。

两个设计前提（来自阅读反馈）：
* 文字稿不是聊天记录，说话人**不换行、不加气泡**，只靠「名字的颜色 + 字号 + 段落间距」
  区分，正文保持统一的深灰；
* 演出提示（换景 / 音乐起 / 音乐停 / 影像）是**演出信息**，要让人一眼看出它不属于台词，
  所以统一成居中、疏字距、浅灰的一行行小字，像话剧剧本的舞台提示。
"""
from __future__ import annotations

import re

# 说话人的名字色：低饱和、够深（白纸上 10.5pt 仍清晰），并按冷暖交替排列，
# 保证「相邻出场的两个人」在色相上分开得最开。
SPEAKER_COLORS = (
    "8A4B41",  # 赭红  暖
    "3A5680",  # 靛    冷
    "77602B",  # 芥    暖
    "3B6355",  # 松绿  冷
    "6A4B78",  # 紫    冷
    "7C5533",  # 棕    暖
    "2F6669",  # 湖蓝  冷
    "4B4B55",  # 玄    中性
)

# 演出提示的种类 → 显示用的中文标签
CUE_LABEL = {
    "music": "音乐",
    "stop": "音乐",
    "scene": "换景",
    "video": "影像",
}

# 只剩停顿/省略号的一行台词：内容为空，靠版面的留白替它说话
_BEAT_CHARS = set("…．.-—–~～?!？！。，、　 \u3000")
# 书名号等不参与「标题是否重复」的比较
_TITLE_NOISE = re.compile(r"[《》〈〉“”‘’\"'·\s　]")


def cue_parts(text: str) -> dict | None:
    """把一条演出提示拆成 (种类, 标签, 值)，供三种排版各自渲染。

    `♪ 幽暗` → 音乐 / 幽暗；`♪ 停` → 音乐 / 停；`◆` → 换景；`▷` → 影像。
    """
    t = (text or "").strip()
    if not t:
        return None
    if t[0] in "♪♫":
        value = t.lstrip("♪♫ 　").strip()
        if not value:
            return {"kind": "music", "label": CUE_LABEL["music"], "value": ""}
        if value in ("停", "停止", "停乐", "收"):
            return {"kind": "stop", "label": CUE_LABEL["stop"], "value": "停"}
        return {"kind": "music", "label": CUE_LABEL["music"], "value": value}
    if t[0] in "▷▶":
        return {"kind": "video", "label": CUE_LABEL["video"], "value": ""}
    if t[0] in "◆◇":
        return {"kind": "scene", "label": CUE_LABEL["scene"], "value": ""}
    return {"kind": "other", "label": "", "value": t}


def story_colors(blocks: list[dict]) -> dict[str, str]:
    """按「出场先后」给一篇里的人物分配颜色，返回 {说话人: 十六进制色}。

    顺序分配而不是按名字哈希：同一篇里最先开口的两个人一定不同色（这对阅读最要紧），
    而每篇第一个出场的人总是同一个颜色，主角在整本书里也就相对稳定。
    """
    colors: dict[str, str] = {}
    for b in blocks:
        if b.get("k") != "d":
            continue
        name = b.get("s") or ""
        if name and name not in colors:
            colors[name] = SPEAKER_COLORS[len(colors) % len(SPEAKER_COLORS)]
    return colors


def color_index(name: str, colors: dict[str, str]) -> int:
    """取 1 基的色号，用来拼 CSS 类名（c1…c8）。"""
    try:
        return SPEAKER_COLORS.index(colors[name]) + 1
    except (KeyError, ValueError):
        return 1


def speaker_column(blocks: list[dict]) -> float:
    """说话人这一列的宽度（em，含冒号），供「悬垂缩进」用。

    台词折行时要落在台词的起始位置，而不是落回说话人的名字底下——这是剧本排版的基本功。
    宽度按本篇最长的名字算，但压在 2–4 字之间：太长会把正文挤成窄条，
    超长的角色名（「近卫局成员A」）就让它自己顶出去一行，别拖着全篇一起变窄。
    """
    longest = 0
    for b in blocks:
        if b.get("k") == "d":
            longest = max(longest, len(b.get("s") or ""))
    n = max(2, min(4, longest))
    # 名字和正文同为 11pt，一个字就是一 em；再留一点空隙把名字和台词分开
    return round(n + 1 + 0.15, 2)


def is_beat(text: str) -> bool:
    """这一行是不是「只有停顿」——`……`、`——`、`？` 之类。

    这类行不承载信息，但承载节奏；排版上单独给一点呼吸，别让沉默挤在句子里。
    """
    t = (text or "").strip()
    if not t or len(t) > 8:
        return False
    return all(ch in _BEAT_CHARS for ch in t)


def title_key(s: str) -> str:
    """比较标题是否重复时的归一化写法（去掉书名号、引号、间隔号、空白）。"""
    return _TITLE_NOISE.sub("", s or "")


def same_title(a: str, b: str) -> bool:
    ka, kb = title_key(a), title_key(b)
    return bool(ka) and ka == kb


def crumb_redundant(crumb: str | None, title: str) -> bool:
    """篇首那行「所属」小字是否已经写在篇名里了。

    `洪炉示岁` × `AF-ST1 洪炉示岁·迎春` 这种一包含的情况，再顶上印一遍就是纯噪音。
    """
    if not crumb:
        return True
    c, t = title_key(crumb), title_key(title)
    if not c or not t:
        return False
    return c in t or t in c


def section_label(name: str, first_title: str) -> str:
    """节标题里如果已经把篇名写进去了，印的时候只留剩下的部分。

    `干员档案·惊蛰（五星术师）` 这一节，篇名就是它开头那四个字；
    页面上再顶一行完整的节名，读者会在三行里看见两次「干员档案」。
    书签 / 目录仍用完整节名，只有版面这一行收窄。
    """
    if not name or not first_title:
        return name
    for sep in ("·", "・", "—", "－", "-", "　", " "):
        if name.startswith(first_title + sep):
            rest = name[len(first_title) + len(sep):].strip()
            return rest or name
        if name.endswith(sep + first_title):
            rest = name[: -len(first_title) - len(sep)].strip()
            return rest or name
    return name


def volume_parts(title: str) -> tuple[str, str, str]:
    """把 `卷五　岁家干员密录（年·夕·令·重岳）` 拆成 (卷次, 卷名, 括注)。

    合订本的卷扉页要分行排：卷次一行小字、卷名一行大字、括号里的补充再小一号。
    整串 26pt 挤在一行会折得很难看（`（年·夕·令` / `·重岳）`）。
    """
    t = (title or "").strip()
    note = ""
    if "（" in t:
        head, rest = t.split("（", 1)
        note = "（" + rest
        t = head.strip()
    num, name = "", t
    for sep in ("　", " "):
        if sep in t:
            num, name = t.split(sep, 1)
            num, name = num.strip(), name.strip()
            break
    return num, name or t, note
