# -*- coding: utf-8 -*-
"""演出提示（场景 / 音乐 / 画面）的读者化处理。

PRTS 剧情页里的演出指令带的是**引擎资源 id**（`bg_rooftop_2`、`$darkness01_loop`），
直接印进正文就是乱码。这里把它们降级成读者能接受的形态：

* 背景切换 `bg_*` → `◆`（真正的场景变化，留一个视觉节拍）
* 镜头/构图 `avg_*`、`image`、`cgitem` 等 → 不显示（纯摄影指令，不进文字稿）
* 音乐 → `♪ 幽暗` 这样的中文曲名；只有系统编号的曲目退化成 `♪`
* 停乐 → `♪ 停`
* 影像 → `▷`

曲名表在 `MUSIC_ZH`；想改显示名直接改这里，或在项目里放 `data/music_zh.json`
覆盖（键为去掉 `$` 与 `_loop` 后的资源名）。
"""
from __future__ import annotations

import json
import os

# 资源名（已去掉 `$` 前缀与 `_loop` / `_intro` 后缀）→ 中文曲名
MUSIC_ZH = {
    # —— 情绪 / 氛围 ——
    "awakening": "觉醒",
    "calamity": "灾厄",
    "calm": "平静",
    "comedy": "谐谑",
    "corrosion": "侵蚀",
    "dignified": "庄重",
    "distressed": "悲苦",
    "drift": "漂荡",
    "escape": "逃亡",
    "escapebattle": "突围",
    "exciting": "激昂",
    "faceoff": "对峙",
    "farce": "闹剧",
    "formal": "正襟",
    "jealous": "妒意",
    "loneliness": "孤寂",
    "mist": "薄雾",
    "nervous": "紧张",
    "newhope01": "新希望",
    "newhope02": "新希望·终",
    "normal": "日常",
    "normal01": "日常",
    "normal02": "日常·二",
    "normalbattle": "寻常战",
    "ouat": "从前",
    "path": "旅途",
    "plot": "叙事",
    "ponder": "沉思",
    "prisonbreak": "越狱",
    "relax": "闲适",
    "saferoom": "安全屋",
    "sjoyasorrow": "悲欣",
    "speedoflight": "光速",
    "storyendjp": "终章",
    "suspenseful": "悬疑",
    "tech": "机械",
    "tense": "紧绷",
    "warchaos": "战乱",
    "warm": "温暖",
    "wasteland": "荒原",
    "loading": "中转",
    # —— 场景 ——
    "bar": "酒吧",
    "darkalley": "暗巷",
    "darkness01": "幽暗",
    "darkness02": "沉暗",
    "darkness_03": "黯夜",
    "indust": "工业",
    "longmenoffice": "龙门官署",
    "m_dia_street": "街市",
    "midautumn": "中秋",
    "monastery_sad": "古寺·悲",
    "nightoflongmen": "龙门夜",
    "taoyuan": "桃源",
    "victorianormal": "维多利亚日常",
    "m_avg_dailylifeofbaizao": "寻常一日",
    "m_avg_destiny": "命途",
    "m_avg_riversnow": "寒江",
    "m_avg_yumennormal": "玉门日常",
    # —— 战斗 ——
    "batmeeting": "战前",
    "chasing": "追逐",
    "kingmouse": "鼠王",
    "longmenbat": "龙门战",
    "longmenbatimp": "龙门战·变",
    "m_bat_epic": "决战",
    "m_bat_imprisonment": "囚困",
    "m_bat_ymmons": "玉门之战",
    # —— 岁家专属 ——
    "chenandwei": "旧识",
    "dontmaketrouble": "莫生事",
    "duskdragon": "夕影",
    "ghosthunter": "寻魉",
    "m_bat_sfsui": "岁·首领战",
    "m_bat_wang": "望",
    "mudrock": "泥岩",
    "suifamilybond": "岁·家缘",
    "suilaststrick": "岁·终局",
}

_CACHE: dict[str, dict] = {}


def _override(root: str | None) -> dict:
    """项目内 `data/music_zh.json` 可覆盖内置曲名表。"""
    if not root:
        return {}
    if root in _CACHE:
        return _CACHE[root]
    extra = {}
    path = os.path.join(root, "data", "music_zh.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                extra = {str(k).lower(): str(v) for k, v in data.items() if v}
        except Exception:
            extra = {}
    _CACHE[root] = extra
    return extra


def normalize_key(key: str) -> str:
    k = (key or "").strip().lower()
    if k.startswith("$"):
        k = k[1:]
    for suf in ("_loop", "_intro"):
        if k.endswith(suf):
            k = k[: -len(suf)]
    return k


def music_label(key: str, root: str | None = None) -> str:
    """返回中文曲名；认不出来就返回空串（正文里只留一个 ♪）。"""
    k = normalize_key(key)
    if not k:
        return ""
    extra = _override(root)
    if k in extra:
        return extra[k]
    return MUSIC_ZH.get(k, "")


def scene_cue(image: str) -> str:
    """背景 id → 显示文本；返回空串表示这条不进正文。"""
    img = (image or "").strip()
    if not img:
        return ""
    low = img.lower()
    # bg_* / 含 bg 前缀的才是真正的换景；avg_* 是镜头构图，属于画面指令
    if low.startswith("bg") or low.startswith("bg_") or "background" in low:
        return "◆"
    return ""


def music_cue(key: str, root: str | None = None) -> str:
    label = music_label(key, root)
    return ("♪ " + label) if label else "♪"


def stop_music_cue() -> str:
    return "♪ 停"


def video_cue() -> str:
    return "▷"
