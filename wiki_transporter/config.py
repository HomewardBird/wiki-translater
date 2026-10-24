# -*- coding: utf-8 -*-
"""项目路径与计划（plan）加载。"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile


def project_root(explicit: str | None = None) -> str:
    """定位项目根目录：默认当前工作目录，若找不到 plans/ 则向上找。"""
    if explicit:
        return os.path.abspath(explicit)
    cur = os.path.abspath(os.getcwd())
    while True:
        if os.path.isdir(os.path.join(cur, "plans")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return os.path.abspath(os.getcwd())


def _bundled_plan_dirs() -> list[str]:
    """打包成 exe / pip 安装后计划文件可能出现的位置。"""
    dirs = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "builtin_plans")]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(os.path.join(meipass, "plans"))
        dirs.append(os.path.join(meipass, "wiki_transporter", "builtin_plans"))
    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(os.path.dirname(sys.executable), "plans"))
        dirs.append(os.path.join(os.path.dirname(sys.executable), "_internal", "plans"))
    return dirs


def builtin_plans() -> list[tuple[str, dict]]:
    """随包分发的整套计划（builtin_plans/*.json）。返回 [(id, data)]，同名取先找到的。"""
    found: dict[str, dict] = {}
    for d in _bundled_plan_dirs():
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".json"):
                continue
            pid = name[:-5]
            if pid in found:
                continue
            try:
                with open(os.path.join(d, name), encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            data.setdefault("id", pid)
            found[pid] = data
    return list(found.items())


class Plan:
    """一本书的构建计划（plans/*.json，或 GUI 生成后存在用户目录）。"""

    def __init__(self, root: str, plan_id: str):
        self.root = root
        self.id = plan_id
        candidates = [os.path.join(root, "plans", plan_id + ".json"),
                      os.path.join(user_data_dir(), "plans", plan_id + ".json")]
        # PyInstaller 单文件 exe：计划文件随包解压到 _MEIPASS，或放在 exe 同目录
        for extra in _bundled_plan_dirs():
            candidates.append(os.path.join(extra, plan_id + ".json"))
        path = next((p for p in candidates if os.path.isfile(p)), None)
        if path is None:
            avail = []
            for d in {os.path.dirname(p) for p in candidates}:
                if os.path.isdir(d):
                    avail += sorted(os.path.splitext(f)[0]
                                    for f in os.listdir(d) if f.endswith(".json"))
            raise SystemExit(
                "找不到计划文件：%s\n可用计划：%s"
                % (candidates[0], "、".join(sorted(set(avail))) or "（无）")
            )
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.plan_path = path
        self._load(data)

    @classmethod
    def from_dict(cls, root: str, data: dict, plan_id: str | None = None) -> "Plan":
        obj = cls.__new__(cls)
        obj.root = root
        obj.id = plan_id or data.get("id") or "book"
        obj.plan_path = None
        obj._load(data)
        return obj

    @classmethod
    def load(cls, root: str, plan_id: str) -> "Plan":
        return cls(root, plan_id)

    def _load(self, data: dict) -> None:
        self.data = data
        self.title = self.data.get("title") or self.id
        # 整理者署名默认写进产物；生成时可以勾掉（data["credit"] = False）。
        # 凡例里那一行在这里现拼，不改老计划文件里的 fanli 原文。
        _credit = self.data.get("credit")
        self.credit = True if _credit is None else bool(_credit)
        self.fanli = list(self.data.get("fanli") or [])
        if self.credit and not any("整理与排版" in line for line in self.fanli):
            self.fanli.append("整理与排版：%s。" % CREDIT)
        self.volumes = self.data.get("volumes") or []
        self.source = self.data.get("source") or ""
        # 封面：cover 给本地图片（字符串，或按卷顺序的列表），给了就不联网抓；
        # cover_fetch 是独立开关，False 时连抓取都不做，直接用本机占位图。
        self.cover = self.data.get("cover") or None
        _fetch = self.data.get("cover_fetch")
        self.cover_fetch = True if _fetch is None else bool(_fetch)

    @property
    def source_id(self) -> str:
        return self.data.get("source_id") or "prts"

    def save(self, path: str | None = None) -> str:
        """把计划写到磁盘（默认用户目录 plans/<id>.json）。"""
        target = path or os.path.join(user_data_dir(), "plans", self.id + ".json")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        self.plan_path = target
        return target

    def apply_credit(self, on: bool) -> None:
        """生成前开关整理者署名：文档属性/封面看 self.credit，凡例那一行在这里加或去。"""
        self.credit = bool(on)
        self.data["credit"] = self.credit
        line = "整理与排版：%s。" % CREDIT
        if self.credit:
            if not any("整理与排版" in x for x in self.fanli):
                self.fanli.append(line)
        else:
            self.fanli = [x for x in self.fanli if "整理与排版" not in x]

    # ---------- 目录 ----------
    @property
    def cache_dir(self) -> str:
        return os.path.join(self.root, "cache", "raw", self.id)

    @property
    def build_dir(self) -> str:
        return os.path.join(self.root, "build", self.id)

    @property
    def books_json(self) -> str:
        return os.path.join(self.build_dir, "books.json")

    def out_dir(self, kind: str) -> str:
        return os.path.join(self.root, "out", self.id, kind)

    # ---------- 页面 ----------
    def pages(self) -> list[str]:
        seen, out = set(), []
        for v in self.volumes:
            for s in v.get("sections", []):
                for t in s.get("pages", []):
                    if t not in seen:
                        seen.add(t)
                        out.append(t)
        return out

    def volume_titles(self) -> list[str]:
        return [v["title"] for v in self.volumes]


def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def user_data_dir() -> str:
    """配置与索引的存放位置：%LOCALAPPDATA%\\WikiTransporter（可被环境变量覆盖）。"""
    env = os.environ.get("WIKITRANSPORTER_HOME")
    if env:
        path = os.path.abspath(env)
    else:
        base = (os.environ.get("LOCALAPPDATA")
                or os.environ.get("XDG_DATA_HOME")
                or os.path.join(os.path.expanduser("~"), ".local", "share"))
        path = os.path.join(base, "WikiTransporter")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = os.path.join(tempfile.gettempdir(), "WikiTransporter")
        os.makedirs(path, exist_ok=True)
    return path


def app_home() -> str:
    """程序主目录：exe 所在目录（打包后）或仓库根目录（源码运行）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- 用户设置
# 整理者署名：默认不写进产物，用户生成时勾选「写入整理者署名」才生效
AUTHOR = "安巢鸟"
CREDIT = "安巢鸟 · 整理"
DEFAULT_AUTHOR = AUTHOR
_DEFAULT_SETTINGS = {"out_root": "", "llm_enabled": "", "llm_base_url": "",
                     "llm_api_key": "", "llm_model": "", "llm_embed_model": "",
                     "llm_embed_local": ""}
_settings_cache: dict | None = None


def settings_path() -> str:
    return os.path.join(user_data_dir(), "settings.json")


def load_settings() -> dict:
    """读取用户设置（产物存放位置），读不到就用默认值。"""
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache
    data = dict(_DEFAULT_SETTINGS)
    try:
        with open(settings_path(), encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for key in _DEFAULT_SETTINGS:
                val = raw.get(key)
                if isinstance(val, bool):
                    data[key] = "1" if val else ""
                elif isinstance(val, str):
                    data[key] = val
    except (OSError, ValueError):
        pass
    _settings_cache = data
    return data


def save_settings(patch: dict) -> dict:
    """合并写入设置，返回保存后的完整设置。"""
    global _settings_cache
    data = dict(load_settings())
    for key in _DEFAULT_SETTINGS:
        if key not in patch:
            continue
        val = patch[key]
        if isinstance(val, bool):
            data[key] = "1" if val else ""
        elif isinstance(val, str):
            data[key] = val.strip()
    if data["out_root"]:
        data["out_root"] = os.path.abspath(
            os.path.expandvars(os.path.expanduser(data["out_root"])))
        ensure_dir(data["out_root"])
    target = settings_path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, target)
    _settings_cache = data
    return data


def reset_settings_cache() -> None:
    global _settings_cache
    _settings_cache = None


def author_name(plan=None) -> str:
    """整理者署名「安巢鸟 · 整理」；plan 关掉署名时返回空串，调用方据此不写这一项。"""
    if plan is not None and not getattr(plan, "credit", True):
        return ""
    return CREDIT


def source_short(plan) -> str:
    """这本书的来源短名（写进总目录、凡例与文档属性）。

    计划里的 source 字段是「显示名（api 地址）」这种写法，这里只取显示名；
    老计划没有 source 时用 PRTS 的显示名，保证不会印错出处。
    """
    text = (getattr(plan, "data", {}) or {}).get("source") or ""
    short = re.split(r"[（(]", text.strip(), maxsplit=1)[0].strip()
    if short:
        return short
    from .sources import PRTS

    return PRTS.name


def source_accent(plan, default: str = "#c8a462") -> str:
    """来源主色（封面装饰线、占位图渐变都用它）。"""
    from .sources import PRTS

    return PRTS.accent or default


def workspace_dir() -> str:
    """产物（out/ cache/ build/）的根目录：环境变量 > 设置里的自定义目录 > 程序目录。"""
    env = os.environ.get("WIKITRANSPORTER_OUT")
    if env:
        return os.path.abspath(env)
    custom = load_settings().get("out_root") or ""
    if custom:
        try:
            os.makedirs(custom, exist_ok=True)
            return custom
        except OSError:
            pass
    return app_home()


def output_root(explicit: str | None = None) -> str:
    """命令行：用户显式指定 > 环境变量 > 设置里的存放位置 > 当前项目根目录。"""
    if explicit:
        return os.path.abspath(explicit)
    env = os.environ.get("WIKITRANSPORTER_OUT")
    if env:
        return os.path.abspath(env)
    custom = load_settings().get("out_root") or ""
    if custom:
        try:
            os.makedirs(custom, exist_ok=True)
            return custom
        except OSError:
            pass
    return project_root()


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def setup_console() -> None:
    """Windows 控制台默认 GBK，统一改成 UTF-8 免得中文报错。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
