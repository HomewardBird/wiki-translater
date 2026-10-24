# -*- coding: utf-8 -*-
"""可选的 LLM 层：查询扩展 + 向量嵌入。

为什么需要这一层
----------------
词法检索认不出「岁家」这种玩家口语集合词；手写别名表只能覆盖有限领域词。
LLM 能把任意说法拆成「概念 / 别名 / 方面」，向量嵌入负责字面不相邻的语义召回。

只认 OpenAI 兼容协议（/chat/completions、/embeddings），所以
Ollama(http://127.0.0.1:11434/v1)、LM Studio(http://127.0.0.1:1234/v1)、
DeepSeek、OpenAI 走同一套代码，本地模型不需要联网也不需要 key。

任何失败（没配、超时、非 JSON、连接被拒）都静默降级成「没开过」：
搜索本身不会因此报错或变慢。
"""
from __future__ import annotations

import array
import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request

from .config import load_settings, save_settings, user_data_dir
from . import smart

KEYS = ("llm_enabled", "llm_base_url", "llm_api_key", "llm_model", "llm_embed_model")
CACHE_NAME = "llm_cache.db"
CACHE_SCHEMA = """
create table if not exists chat_cache(q text primary key, json text, at real);
"""


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

def config() -> dict:
    s = load_settings()
    base = str(s.get("llm_base_url") or "").rstrip("/")
    local = str(s.get("llm_embed_local") or "").strip().lower() in ("1", "true", "yes", "on")
    embed_model = str(s.get("llm_embed_model") or "")
    if local:
        # 本地模型优先：文件下齐了就把模型名顶上，deep.py 里那些门禁照旧只看 embed_model
        try:
            from . import localembed

            if localembed.ready():
                embed_model = localembed.NAME
        except Exception:
            pass
    return {
        "enabled": str(s.get("llm_enabled") or "").strip().lower() in ("1", "true", "yes", "on"),
        "base_url": base,
        "api_key": str(s.get("llm_api_key") or ""),
        "model": str(s.get("llm_model") or ""),
        "embed_model": embed_model,
        "embed_local": local,
    }


def configured(cfg: dict | None = None) -> bool:
    c = cfg or config()
    return bool(c["base_url"] and c["model"])


def enabled(cfg: dict | None = None) -> bool:
    c = cfg or config()
    return bool(c["enabled"] and configured(c))


def status() -> dict:
    c = config()
    return {"enabled": bool(c["enabled"]), "configured": configured(c),
            "base_url": c["base_url"], "model": c["model"],
            "embed_model": c["embed_model"], "cache": _cache_count()}


# --------------------------------------------------------------------------
# HTTP（标准库，无第三方依赖）
# --------------------------------------------------------------------------

def _post(path: str, payload: dict, timeout: float = 20.0) -> dict | None:
    c = config()
    if not c["base_url"]:
        return None
    url = c["base_url"] + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if c["api_key"]:
        req.add_header("Authorization", "Bearer " + c["api_key"])
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
        out = json.loads(body)
        return out if isinstance(out, dict) else None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError, json.JSONDecodeError):
        return None


def _first_text(d: dict) -> str:
    try:
        ch = d["choices"][0]
        msg = ch.get("message") or {}
        text = msg.get("content")
        if not text:
            text = ch.get("text") or ""
        return str(text or "")
    except (KeyError, IndexError, TypeError):
        return ""


def chat(prompt: str, system: str | None = None, timeout: float = 20.0) -> str | None:
    c = config()
    if not configured(c):
        return None
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    d = _post("/chat/completions",
              {"model": c["model"], "messages": msgs, "temperature": 0,
               "stream": False}, timeout=timeout)
    if not d:
        return None
    return _first_text(d) or None


def _parse_obj(text: str | None) -> dict | None:
    """从模型输出里抠出第一个 JSON 对象（模型常加 ```json 或前言）。"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1] if len(t.split("```")) > 1 else t
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        out = json.loads(t[i:j + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    return out if isinstance(out, dict) else None


# --------------------------------------------------------------------------
# 缓存
# --------------------------------------------------------------------------

def _cache_conn() -> sqlite3.Connection:
    os.makedirs(user_data_dir(), exist_ok=True)
    con = sqlite3.connect(os.path.join(user_data_dir(), CACHE_NAME), timeout=10)
    con.executescript(CACHE_SCHEMA)
    return con


def _cache_count() -> int:
    try:
        con = _cache_conn()
        try:
            return con.execute("select count(*) from chat_cache").fetchone()[0]
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return 0


def _cache_get(q: str) -> dict | None:
    if not q:
        return None
    try:
        con = _cache_conn()
        try:
            row = con.execute("select json from chat_cache where q=?", (q,)).fetchone()
        finally:
            con.close()
        return json.loads(row[0]) if row else None
    except (sqlite3.DatabaseError, ValueError):
        return None


def _cache_put(q: str, val: dict) -> None:
    try:
        con = _cache_conn()
        try:
            con.execute("insert or replace into chat_cache(q,json,at) values(?,?,?)",
                        (q, json.dumps(val, ensure_ascii=False), time.time()))
            con.commit()
        finally:
            con.close()
    except sqlite3.DatabaseError:
        pass


# --------------------------------------------------------------------------
# 查询理解
# --------------------------------------------------------------------------

_PROMPT = (
    "你是《明日方舟》剧情检索助手。用户查询：{q}\n"
    "只输出一个 JSON 对象，不要解释，字段如下：\n"
    '{{"concepts":["核心概念,1-3个"],"aliases":["同义或上位词/活动名/角色名,最多12个"],'
    '"aspects":["用户想了解的角度,如 历史/人物/事件/结局"]}}\n'
    "要求：中文；aliases 必须是《明日方舟》里真实存在的活动名、角色名或地名，"
    "不许编造作品；不确定就给空数组。"
)


def expand_query(q: str, timeout: float = 20.0) -> dict:
    """查询 → {concepts, aliases, aspects}；先查缓存，没配 LLM 返回空结构。"""
    empty = {"concepts": [], "aliases": [], "aspects": [], "source": "none"}
    q = (q or "").strip()
    if not q:
        return empty
    cached = _cache_get(q)
    if cached:
        # 接口刚失败过就先别等：否则每次改查询词都要多花一次超时
        if cached.get("failed_at") and time.time() - float(cached["failed_at"]) < 600:
            return dict(empty)
        cached["source"] = "cache"
        return cached
    if not enabled():
        return empty
    obj = _parse_obj(chat(_PROMPT.format(q=q), timeout=timeout))
    if not obj:
        _cache_put(q, {"concepts": [], "aliases": [], "aspects": [],
                       "failed_at": time.time()})
        return empty

    def lst(key: str, cap: int) -> list[str]:
        raw = obj.get(key)
        if not isinstance(raw, list):
            return []
        out = []
        for x in raw:
            s = str(x).strip()
            if 2 <= len(s) <= 12 and s not in out:
                out.append(s)
        return out[:cap]

    val = {"concepts": lst("concepts", 3), "aliases": lst("aliases", 12),
           "aspects": lst("aspects", 4)}
    if val["concepts"] or val["aliases"]:
        _cache_put(q, val)
        val["source"] = "llm"
    else:
        val["source"] = "none"
    return val


# --------------------------------------------------------------------------
# 向量嵌入
# --------------------------------------------------------------------------

def embed(texts: list[str], timeout: float = 40.0) -> list[list[float]] | None:
    """把一批文本编码成向量。优先本地模型（不联网），其次 OpenAI 兼容 /embeddings。

    返回与 texts 等长的向量列表；都没配好返回 None。
    """
    c = config()
    if not texts:
        return None
    if c.get("embed_local"):
        from . import localembed

        vecs = localembed.embed(texts)
        if vecs is not None:
            return vecs
    api_model = str(load_settings().get("llm_embed_model") or "")
    if not (c["base_url"] and api_model):
        return None
    d = _post("/embeddings", {"model": api_model, "input": texts},
              timeout=timeout)
    if not d:
        return None
    rows = d.get("data")
    if not isinstance(rows, list) or len(rows) != len(texts):
        return None
    out = []
    for r in rows:
        v = (r or {}).get("embedding")
        if not isinstance(v, list) or not v:
            return None
        try:
            out.append([float(x) for x in v])
        except (TypeError, ValueError):
            return None
    return out


def pack(vec: list[float]) -> bytes:
    return array.array("f", vec).tobytes()


def unpack(blob: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(blob)
    return a


def cache_key(text: str) -> str:
    h = hashlib.sha1()
    h.update(text.encode("utf-8", "replace"))
    return h.hexdigest()


def save_settings_patch(patch: dict) -> dict:
    """只接受 llm_* 键，值统一转字符串/布尔。"""
    clean = {}
    for k, v in (patch or {}).items():
        if k not in KEYS:
            continue
        if k == "llm_enabled":
            clean[k] = bool(v)
        else:
            clean[k] = str(v or "").strip().rstrip("/") if k == "llm_base_url" else str(v or "").strip()
    return save_settings(clean)


def _doc_meta() -> dict:
    """预热 smart 索引连接（deep.py 会复用）。"""
    try:
        smart.stats()
    except Exception:
        pass
    return {}
