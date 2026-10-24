# -*- coding: utf-8 -*-
"""深层检索：先认出「岁家」是什么，再分词法 / 概念 / 向量三路召回。

为什么不能只靠 trigram
----------------------
FTS5 trigram 只会做字面子串匹配。「岁家」「岁兽」是玩家口语集合词，正文里几乎
不出现，召回必然是 0；「岁的历史发展」拆出来只剩一个「岁」字，连 trigram 都
匹配不了。要回答「想全面理解这方面该看什么」，必须先有概念层。

三层召回
--------
1. 别名/概念表（离线，手写 + 计划文件反推）：岁家 → 年/夕/令/重岳/洪炉示岁/…
2. 实体共现图（离线，复用 smart 的 entities/doc_entities）：岁 → 大炎/天师/…
3. LLM 扩展（可选，OpenAI 兼容）+ 向量嵌入（可选，/embeddings）

融合用 RRF（reciprocal rank fusion，k=60），这是混合检索里最省事的做法：
不需要把不同来源的分数校准到同一量纲。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time

from .config import user_data_dir
from . import llm, smart
from .catalog import norm

# 手写领域别名。只放「玩家口语 → 作品内真实专名」，宁缺毋滥：
# 编进去的每个词都会真的拿去搜，编错只会制造噪声。
ALIASES: dict[str, list[str]] = {
    "岁家": ["岁", "重岳", "黍", "颉", "洪炉示岁",
             "画中人", "将进酒", "登临意", "怀黍离", "惊蛰", "相见欢",
             "镜中集", "辞岁行", "岁相", "岁的界园志异"],
    "岁": ["岁家", "洪炉示岁", "画中人", "将进酒", "登临意",
           "怀黍离", "惊蛰", "相见欢", "镜中集", "辞岁行", "岁相"],
    "岁兽": ["岁", "岁家", "洪炉示岁", "画中人", "将进酒", "辞岁行"],
    "萨卡兹": ["卡兹戴尔", "特雷西斯", "特蕾西娅", "巴别塔", "提卡兹",
               "萨科塔", "内战", "维多利亚", "摄政王", "伦蒂尼姆", "赫德雷",
               "伊内丝", "曼弗雷德", "王庭", "魔王", "殿下"],
    "卡兹戴尔": ["萨卡兹", "特雷西斯", "特蕾西娅", "内战", "巴别塔", "提卡兹",
                 "王庭", "殿下"],
    "大炎": ["岁", "岁家", "天师", "御史", "玉门", "百灶", "龙门", "洪炉示岁"],
    "深海": ["阿戈尔", "海嗣", "罗德岛", "深海猎人", "伊比利亚", "大海"],
    "阿戈尔": ["深海", "海嗣", "伊比利亚", "深海猎人"],
    "源石": ["源石技艺", "矿石病", "感染者", "天灾", "源石虫"],
    "矿石病": ["感染者", "源石", "源石技艺", "整合运动"],
    "感染者": ["矿石病", "整合运动", "源石", "切尔诺伯格"],
    "罗德岛": ["凯尔希", "阿米娅", "博士", "干员", "整合运动"],
    "整合运动": ["切尔诺伯格", "感染者", "塔露拉", "龙门"],
    "乌萨斯": ["切尔诺伯格", "整合运动", "皇帝", "乌萨斯帝国"],
    "维多利亚": ["萨卡兹", "摄政王", "特雷西斯", "伦蒂尼姆", "巴别塔"],
    "莱塔尼亚": ["巫王", "源石技艺", "高塔", "卡兹戴尔"],
    "谢拉格": ["蔓珠院", "雪境", "谢拉格"],
    "龙门": ["近卫局", "陈", "炎", "整合运动", "百灶"],
}

# 只问「哪个方面」，不参与字面检索
_ASPECTS = ("历史", "发展", "来历", "起源", "渊源", "关系", "人物", "角色",
            "事件", "结局", "设定", "世界观", "背景", "介绍", "概况", "故事",
            "经历", "时间线", "顺序", "该看", "先看", "怎么理解", "是什么")
_ASK = ("我想了解", "我想知道", "想了解", "想知道", "了解一下", "了解",
        "关于", "有哪些", "哪个", "哪些", "怎么样", "怎么", "如何", "为什么",
        "相关", "剧情", "内容", "资料", "情况", "全面")
_CJK = re.compile(r"[\u4e00-\u9fff]{2,10}")

# 「历史/发展」这类方面词只补同义说法，不参与核心概念判定
ASPECT_WORDS = {
    "历史": ["历史", "起源", "过去", "百年", "千年", "记载", "传说", "兴衰",
             "建立", "覆灭"],
    "发展": ["发展", "演变", "后来", "如今", "走向"],
    "人物": ["出身", "身份", "干员", "角色"],
    "事件": ["战争", "战役", "冲突", "起因", "经过"],
    "结局": ["结局", "结果", "最终", "结束"],
    "设定": ["设定", "背景", "世界观", "介绍", "概况"],
}


def _clean(q: str) -> str:
    s = (q or "").strip()
    for w in sorted(_ASK, key=len, reverse=True):
        s = s.replace(w, "")
    return re.sub(r"[\s的了吗呢之，。！？、,.!?]", "", s)


def understand(q: str) -> dict:
    """查询 → {core, concepts, aliases, aspects, source}"""
    s = (q or "").strip()
    aspects = [a for a in _ASPECTS if a in s]
    core = _clean(s)
    for a in sorted(_ASPECTS, key=len, reverse=True):
        core = core.replace(a, "")
    core = core.strip()

    if not core:
        runs = sorted(_CJK.findall(s), key=len, reverse=True)
        core = runs[0] if runs else s

    # 「岁的历史发展」剩下的「岁」靠别名表补全；「岁家的」同理
    concept = core
    alias_terms: list[str] = []
    for key in ALIASES:
        if key and (core == key or (len(core) >= 2 and (core in key or key in core))):
            concept = key
            alias_terms = ALIASES[key]
            break
    if not alias_terms:
        alias_terms = ALIASES.get(core, [])

    terms: list[str] = []
    for t in [core] + list(alias_terms):
        if t and t not in terms and 2 <= len(t) <= 10:
            terms.append(t)

    return {"query": s, "core": concept if len(core) < 2 else core,
            "concept": concept, "terms": terms, "aspects": aspects,
            "source": "alias" if alias_terms else "raw"}


# --------------------------------------------------------------------------
# 实体共现图（离线）
# --------------------------------------------------------------------------

def graph_terms(terms: list[str], limit: int = 12) -> list[dict]:
    """和查询词在同一篇里共同出现的实体 —— 「岁」→ 大炎/天师/年/夕。"""
    if not terms:
        return []
    try:
        con = smart.connect()
    except sqlite3.DatabaseError:
        return []
    # 只用最靠前的两个种子：扩展词越多，共现越容易漂到隔壁的剧情线
    # （「岁家」→ 大炎 → 玉门，就不对了）
    out: dict[str, dict] = {}
    try:
        for seed in terms[:2]:
            try:
                rows = con.execute(
                    "select b.term, count(*) docs, sum(b.n) sc "
                    "from doc_entities a join doc_entities b on b.doc_id = a.doc_id "
                    "join entities e on e.term = b.term "
                    "where a.term = ? and b.term <> ? "
                    "group by b.term having docs >= 2 "
                    "order by docs * 1.0 / (e.df + 8) desc limit 20",
                    (seed, seed)).fetchall()
            except sqlite3.OperationalError:
                rows = []
            for term, docs, sc in rows:
                if len(term) < 2 or len(term) > 6 or term in terms:
                    continue
                cur = out.get(term)
                if cur is None or docs > cur["docs"]:
                    out[term] = {"term": term, "docs": int(docs), "score": int(sc),
                                 "reason": "cooccur"}
    finally:
        con.close()
    return sorted(out.values(), key=lambda x: -x["docs"])[:limit]


# --------------------------------------------------------------------------
# 向量层（可选；未配置 embed 模型时整层跳过）
# --------------------------------------------------------------------------

VEC_NAME = "vec_index.db"
_VEC_SCHEMA = """
create table if not exists vec(doc_id integer primary key, dim integer, blob blob,
                               model text, at real);
create table if not exists vec_meta(k text primary key, v text);
"""


def vec_path() -> str:
    return os.path.join(user_data_dir(), VEC_NAME)


def vec_conn() -> sqlite3.Connection:
    os.makedirs(user_data_dir(), exist_ok=True)
    con = sqlite3.connect(vec_path(), timeout=15)
    con.executescript(_VEC_SCHEMA)
    return con


def vec_stats() -> dict:
    try:
        con = vec_conn()
        try:
            n = con.execute("select count(*) from vec").fetchone()[0]
            row = con.execute("select v from vec_meta where k='model'").fetchone()
            return {"vectors": n, "model": row[0] if row else ""}
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return {"vectors": 0, "model": ""}


def build_vectors(progress=None, batch: int = 16) -> dict:
    """给每篇建一条向量：标题 + 说话人 + 正文前 1200 字。"""
    if not llm.config()["embed_model"]:
        return {"ok": False, "error": "没有配置向量模型（llm_embed_model）"}
    con = smart.connect()
    try:
        rows = con.execute(
            "select d.id, d.title, substr(b.body, 1, 1200) from docs d "
            "join docs_fts b on b.rowid = d.id").fetchall()
    finally:
        con.close()
    total = len(rows)
    vcon = vec_conn()
    done = 0
    try:
        for i in range(0, total, batch):
            chunk = rows[i:i + batch]
            texts = [("《%s》\n%s" % (t, body))[:2000] for _, t, body in chunk]
            vecs = llm.embed(texts)
            if not vecs:
                return {"ok": False, "error": "向量接口没有返回结果", "done": done}
            for (doc_id, _, _), v in zip(chunk, vecs):
                vcon.execute(
                    "insert or replace into vec(doc_id,dim,blob,model,at) values(?,?,?,?,?)",
                    (doc_id, len(v), llm.pack(v), llm.config()["embed_model"], time.time()))
            vcon.commit()
            done = i + len(chunk)
            if progress:
                progress(done, total, "向量化 %d/%d" % (done, total))
    finally:
        vcon.close()
    # 记下这次用的模型名，设置页/状态里能显示「已建 2132 条 · bge-small-zh-v1.5」
    try:
        vcon = vec_conn()
        try:
            vcon.execute("insert or replace into vec_meta(k,v) values('model',?)",
                         (llm.config()["embed_model"],))
            vcon.commit()
        finally:
            vcon.close()
    except sqlite3.DatabaseError:
        pass
    return {"ok": True, "vectors": done, "model": llm.config()["embed_model"]}


def vec_search(q: str, limit: int = 60) -> list[tuple[int, float]]:
    """查询向量 → [(doc_id, cosine)]；没配模型或没建表返回空。"""
    if not llm.config()["embed_model"]:
        return []
    vecs = llm.embed([q])
    if not vecs:
        return []
    qv = vecs[0]
    norm = sum(x * x for x in qv) ** 0.5 or 1.0
    out: list[tuple[int, float]] = []
    try:
        con = vec_conn()
        try:
            for doc_id, blob in con.execute("select doc_id, blob from vec"):
                v = llm.unpack(blob)
                if len(v) != len(qv):
                    continue
                dot = 0.0
                for a, b in zip(qv, v):
                    dot += a * b
                nb = sum(x * x for x in v) ** 0.5 or 1.0
                out.append((doc_id, dot / (norm * nb)))
        finally:
            con.close()
    except (sqlite3.DatabaseError, ValueError):
        return []
    out.sort(key=lambda kv: -kv[1])
    return out[:limit]


# --------------------------------------------------------------------------
# 组合检索
# --------------------------------------------------------------------------

def _rrf(rank_lists: list[list[int]], k: float = 60.0) -> dict[int, float]:
    score: dict[int, float] = {}
    for ranks in rank_lists:
        for i, doc_id in enumerate(ranks):
            score[doc_id] = score.get(doc_id, 0.0) + 1.0 / (k + i)
    return score


def search(q: str, limit: int = 200) -> dict:
    """三层融合。无网无 key 时只用别名 + 共现两层，耗时保持秒级。"""
    t0 = time.time()
    und = understand(q)
    terms = list(und["terms"])
    warnings: list[str] = []
    mode = "offline"

    exp = llm.expand_query(q)
    for t in (exp.get("aliases") or []) + (exp.get("concepts") or []):
        if t and t not in terms and 2 <= len(t) <= 12:
            terms.append(t)
    if exp.get("source") in ("llm", "cache"):
        mode = "llm"
    elif llm.config()["enabled"]:
        warnings.append("LLM 没返回结果，已退回别名表扩展")

    for aspect, words in ASPECT_WORDS.items():
        if aspect not in und["aspects"]:
            continue
        for w in words:
            if w not in terms and len(w) >= 2:
                terms.append(w)

    # 共现扩展：当查询已通过 ALIASES 命中概念（source="alias"）时跳过——
    # 别名表已提供精确的活动名召回（洪炉示岁/将进酒…），共现扩展会从正文拉来
    # 「玉门/左宣辽/太傅」等词，它们把非岁家剧情（喧闹法则/邂逅潮声）召回进来，
    # 反而制造噪声。只有无别名表匹配的泛查询（萨卡兹/维多利亚 这种靠共现补全的）才用。
    if und["source"] != "alias":
        graph = graph_terms(terms, limit=10)
        for g in graph:
            if g["term"] not in terms and 2 <= len(g["term"]) <= 8:
                terms.append(g["term"])
    else:
        graph = []

    lex = smart.search_text(q, limit=min(limit, 200), extra_terms=terms,
                            core_override=und["concept"] or None)
    hits = lex.get("hits") or []

    vhits = vec_search(q) if llm.config()["embed_model"] else []
    if vhits:
        lex_ids: dict[str, int] = {}
        con = smart.connect()
        try:
            for h in hits:
                row = con.execute("select id from docs where title=?", (h["title"],)).fetchone()
                if row:
                    lex_ids[h["title"]] = row[0]
        finally:
            con.close()
        fused = _rrf([[lex_ids[h["title"]] for h in hits if h["title"] in lex_ids],
                      [d for d, _ in vhits]])
        titles = {v: k for k, v in lex_ids.items()}
        if fused:
            con = smart.connect()
            try:
                extra = []
                for doc_id, sc in sorted(fused.items(), key=lambda kv: -kv[1])[:limit]:
                    if doc_id in titles:
                        continue
                    # 别名表匹配的查询（如「岁家」已展开到洪炉示岁/将进酒…）：
                    # 正文里没有任何 terms 命中的篇目不追加——vector 语义近似
                    # 会把喧闹法则/邂逅潮声等不相关活动拉进来。
                    # 只有无别名表匹配的泛查询（source != "alias"）才用 vector 补全。
                    if und["source"] == "alias":
                        continue
                    row = con.execute(
                        "select d.id, d.title, d.name, d.code, d.plan, "
                        "substr(b.body,1,120) from docs d join docs_fts b "
                        "on b.rowid=d.id where d.id=?", (doc_id,)).fetchone()
                    if row:
                        code = row[3] or ""
                        m = smart._ACT_RE.match(code)
                        head = m.group(1).upper() if m else ""
                        extra.append({"title": row[1], "name": row[2] or row[1],
                                      "code": code, "plan": row[4] or "",
                                      "score": round(sc * 100, 2),
                                      "snippet": (row[5] or "").replace("\n", " ")[:110],
                                      "arc": smart._act_name_map().get(head, "")})
            finally:
                con.close()
            seen = {h["title"] for h in hits}
            hits = hits + [h for h in extra if h["title"] not in seen]
            mode = "vector" if mode == "offline" else "llm+vector"

    tree = list(graph)
    for t in (lex.get("tree") or []):
        if all(t["term"] != x["term"] for x in tree):
            tree.append({"term": t["term"], "docs": t.get("docs", 0),
                         "score": t.get("score", 0), "reason": "cooccur"})
    for t in terms:
        if t not in [x["term"] for x in tree] and t != und["core"]:
            tree.append({"term": t, "docs": 0, "score": 0, "reason": "alias"})

    # 概念相关总篇数：terms（核心 + 别名 + 共现）在正文里任一 LIKE 命中的 doc_id
    # 并集。trigram 对 2 字词返回 0，但 LIKE 能数到真实出现，所以这里用 LIKE。
    matched_total = 0
    scored = [und["concept"]] + terms
    if scored:
        try:
            con = smart.connect()
            try:
                ids: set[int] = set()
                for t in scored:
                    if not t or len(t) < 2:
                        continue
                    for row in con.execute(
                        "select d.id from docs d join docs_fts b on b.rowid=d.id "
                        "where b.body like ?", ("%" + t + "%",)).fetchall():
                        ids.add(row[0])
                matched_total = len(ids)
            finally:
                con.close()
        except sqlite3.DatabaseError:
            matched_total = 0

    # 去重：同一篇剧情在多个 plan 的 raw 目录下各有一份 txt，索引里就有多行，
    # 标题完全相同。按 norm(title) 去重，保留分数高（排在前）的那条。
    # 去重在排序前做：hits 此时还是分数序，保留的第一条就是最高分。
    seen_titles: set[str] = set()
    deduped = []
    for h in hits:
        k = norm(h.get("title") or "")
        if k in seen_titles:
            continue
        seen_titles.add(k)
        deduped.append(h)
    hits = deduped
    # 排序：发布时间线为主（_route_key），让同活动篇目连在一起、活动间按
    # CN 服实装时间排——洪炉示岁(2020) → 红松林 → 将进酒 → 怀黍离 → 揭幕者们。
    # _route_key 相同时（同活动同章节的多篇）Python sort 稳定，保持原 score 序。
    hits.sort(key=smart._route_key)

    # 活动补全：只补「相关度够」的活动，并把单句客串台词造成的 0.40 散落命中丢掉。
    hits = _complete_acts(hits, limit, terms)

    return {
        "matched_total": matched_total,
        "intent": {"query": q, "core": und["concept"], "aspects": und["aspects"],
                   "expansions": terms[:24], "concept": und["concept"],
                   "source": und["source"]},
        "hits": hits[:limit],
        "tree": tree[:16],
        "timeline": lex.get("timeline") or [],
        "corpus": lex.get("corpus") or {},
        "mode": mode,
        "llm": llm.status(),
        "vector": vec_stats(),
        "warnings": warnings,
        "elapsed": round(time.time() - t0, 2),
    }


def _complete_acts(hits: list[dict], limit: int,
                   terms: list[str] | tuple[str, ...] = ()) -> list[dict]:
    """只补「查询点名过的活动」和「多次实质命中的活动」，其余整条丢掉。

    命中 1 篇就补全整卷，会把一句客串台词放大成噪声：搜「岁家」时火蓝之心只
    出现一句惊蛰台词（仅靠 2 字别名 LIKE 命中，score≈0.40），补全后 1 篇变 25 篇，
    众生行记 1→24、落叶逐火 1→19 同理。规则：
      · 活动名本身出现在查询别名里（洪炉示岁/画中人/将进酒…）→ 整卷补全
      · 或该活动有 ≥2 篇 score>0.5 的实质命中（3 字以上词真命中）→ 整卷补全
      · 不满足的活动，它那些散落命中（含 0.40 的客串）一并丢掉；
        主线/无代号篇目只留 score>0.5 的真命中
    """
    if not hits:
        return hits
    from . import themes
    acts = themes.load()
    if not acts:
        return hits
    # 代号前缀 → 活动全部篇目
    act_pages: dict[str, list[str]] = {}
    for a in acts:
        for code in (a.get("codes") or []):
            code = str(code).upper()
            if code and not code.isdigit():
                act_pages[code] = list(a.get("pages") or [])
    # 查询别名里点名的活动：岁家 → AF 洪炉示岁 / WR 画中人 / IW 将进酒 …
    long_terms = [t for t in terms if len(t) >= 3]
    named: set[str] = set()
    for a in acts:
        names = [n for n in (a.get("name"), a.get("short")) if n]
        for code in (a.get("codes") or []):
            head = str(code).upper()
            if not head or head.isdigit():
                continue
            if any(t in nm or nm in t for nm in names for t in long_terms):
                named.add(head)
    # 0.40 是 smart.py 短别名 LIKE 的固定权重：只有 2 字角色名碰到一句台词
    STRONG = 0.5
    from .catalog import norm
    act_hits: dict[str, list[dict]] = {}
    for h in hits:
        code = h.get("code") or ""
        m = smart._ACT_RE.match(code)
        if m:
            head = m.group(1).upper()
            act_hits.setdefault(head, []).append(h)

    def qualifies(head: str) -> bool:
        if head in named:
            return True
        # 新规则：命中篇数 ≥ 该活动总篇数的 1/2（向上取整）才补全整卷
        hh = act_hits.get(head) or []
        pages = act_pages.get(head, [])
        if not pages:
            return False
        threshold = (len(pages) + 1) // 2  # 5 → 3, 7 → 4, 6 → 3
        return sum(1 for h in hh if (h.get("score") or 0) > STRONG) >= threshold

    kept: list[dict] = []
    for h in hits:
        m = smart._ACT_RE.match(h.get("code") or "")
        if not m:
            if (h.get("score") or 0) > STRONG:
                kept.append(h)
            continue
        if qualifies(m.group(1).upper()):
            kept.append(h)
    # 补全后仍不足以说明主题的散落命中已丢弃；没有任何可留命中时返回空，
    # 前端 smartBlock 收到空 hits 就整块不渲染（连标题一起消失）。
    seen_titles = {norm(h.get("title") or "") for h in kept}
    extra: list[dict] = []
    for head, hh in act_hits.items():
        if not hh or not qualifies(head):
            continue
        pages = act_pages.get(head)
        if not pages:
            continue
        for t in pages:
            k = norm(t)
            if k in seen_titles:
                continue
            # themes.json 的篇目标题可能和索引里的略有不同，尽量复用已命中篇的信息
            code, name = smart._split_title(t)
            extra.append({"title": t, "name": name or t, "code": code or "",
                          "plan": "", "score": 0.0, "exact": False,
                          "arc": smart._act_name_map().get(head, "")})
            seen_titles.add(k)
    if not kept and not extra:
        return []
    if not extra:
        return kept[:limit]
    # 补进来的篇目排到它所属活动的命中篇目旁边，整体重新按 _route_key 排
    out = kept + extra
    out.sort(key=smart._route_key)
    return out[:limit]


def explain(q: str) -> dict:
    """给 GUI / 调试用：这串查询被理解成了什么。"""
    und = understand(q)
    exp = llm.expand_query(q)
    return {"understood": und, "llm": exp, "llm_status": llm.status()}
