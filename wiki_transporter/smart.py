# -*- coding: utf-8 -*-
"""正文全文检索：让「萨卡兹」这种世界观词也能搜到剧情。

为什么需要这个模块
------------------
catalog.search() 只对页面标题（篇名、代号、干员密录名）打分。玩家真正会搜的
是「萨卡兹」「源石技艺」「维多利亚」这种世界观词，它们从不出现在篇名里，
所以标题索引下的召回必然是 0。要回答「想了解萨卡兹的历史发展该看哪些剧情」，
必须先有正文索引。

三层结构
--------
1. 正文索引   cache/raw/<plan>/*.txt → 去演出脚本指令 → SQLite FTS5(trigram)
2. 意图拆解   「萨卡兹的历史发展」→ 核心词「萨卡兹」+ 方面「历史/发展」→ 扩展词
3. 知识关联   实体共现树（卡兹戴尔、内战、特雷西斯…）+ 章节时间线

为什么是 trigram
----------------
实测结论（dsh-knowledge-sqlite 的 EXPERIMENTS，512/522 条语料）：
    FTS5 默认 unicode61 对中文子串召回 0%
    trigram 纯词法           65%
    trigram + 查询扩展       88%
unicode61 按空格切词，中文整段是一「词」，子串永远匹配不上；trigram 按字符
三元组建索引，「萨卡兹」正好一个 trigram 短语，是中文子串检索的正确解法。
代价：查询串短于 3 个字符时 trigram 无法匹配，回退到 LIKE 全扫。

工程细节（照搬上面那份实现里踩过的坑）
--------------------------------------
- upsert 用 INSERT ... ON CONFLICT DO UPDATE 保留 rowid，不能用 INSERT OR REPLACE，
  否则 rowid 变化会让 FTS 行失联。
- 长查询的 trigram 数会爆炸，按 idf 优先截断到 max_query_trigrams 个。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from html import escape as _esc

from .config import app_home, user_data_dir
from .parse import parse_story

INDEX_VERSION = 2
DB_NAME = "text_index.db"

# 查询里出现这些词，说明用户问的是「某个东西的某个方面」，不是篇名。
# 剥掉之后剩下的才是要在正文里找的核心词（萨卡兹的历史发展 → 萨卡兹）。
_ASPECT_WORDS = {
    "历史": ["历史", "起源", "由来", "渊源", "过去", "曾经", "古老", "年代",
             "百年", "千年", "记载", "传说", "兴衰", "覆灭", "建立", "诞生"],
    "发展": ["发展", "演变", "变化", "逐渐", "后来", "如今", "现在", "走向"],
    "关系": ["关系", "关联", "联系", "渊源"],
    "人物": ["人物", "谁", "干员", "角色", "出身", "身份"],
    "事件": ["事件", "战争", "战役", "内战", "冲突", "经过", "起因", "导火索"],
    "结局": ["结局", "结果", "最终", "最后", "结束", "下场"],
    "设定": ["设定", "背景", "世界观", "介绍", "是什么", "是什么样", "什么样",
             "概况", "简介"],
}
_ASPECT_KEYS = list(_ASPECT_WORDS)
# 「介绍/了解/想知道/相关」这类词只是问法，不参与扩展
_ASK_WORDS = ("我想了解", "我想知道", "想知道", "想了解", "了解一下", "关于",
              "有哪些", "哪个", "哪些", "怎么", "如何", "为什么", "相关",
              "剧情", "内容", "资料", "情况", "的", "一下")

# 实体候选的过滤：含这些字的连续汉字串基本是虚词组合。
# 注意：这里只能放真虚词。早期版本把「多/大/中/上/小/人」也当分隔符，结果
# 「维多利亚」被切成「维」+「利亚士兵」、「多萝西」被切成两半，实体表里专名
# 直接消失 —— 专名里的实义字必须保留。
_STOP_CHARS = set(
    "的了是在不和与或把被给让着过就都也又还些么呢吧啊吗"
    "我你他她它们这那哪谁怎为以对从到于其而且但如所因可没"
)
_STOP_WORDS = {
    "这个", "那个", "什么", "怎么", "这样", "那样", "这里", "那里", "自己",
    "我们", "你们", "他们", "她们", "它们", "现在", "刚才", "已经", "还是",
    "就是", "可是", "但是", "因为", "所以", "如果", "虽然", "然后", "于是",
    "一个", "一些", "一种", "一样", "这些", "那些", "有些", "很多", "所有",
    "上面", "下面", "中间", "大家", "别人", "左右", "前后", "里面", "外面",
    "时候", "地方", "东西", "事情", "问题", "样子", "声音", "眼睛", "手",
    "知道", "觉得", "认为", "看到", "听见", "说话", "回答", "问道", "说道",
    "没有", "不是", "不会", "不能", "可以", "应该", "需要", "开始", "结束",
    "起来", "出来", "过来", "下来", "上去", "进去", "回来", "过去",
    "等等", "哈哈", "哈哈哈", "抱歉", "谢谢", "再见", "听见", "看见",
}
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,8}")
# 虚词不当过滤条件、当分隔符：正文里「的萨卡兹吧」必须切出「萨卡兹」，
# 否则专名永远被旁边的「的/了/是」连坐丢掉（第一版就是这么把实体表做成空的）。
_STOP_RE = re.compile("[" + re.escape("".join(sorted(_STOP_CHARS))) + "]")
_SUFFIX_RE = re.compile(r"_(NBT|BEG2|END2|BEG|END)$", re.I)


# --------------------------------------------------------------------------
# 语料发现
# --------------------------------------------------------------------------

def corpus_roots() -> list[str]:
    """所有可能放 cache/raw 的根目录。"""
    roots = [app_home(), user_data_dir()]
    seen, out = set(), []
    for r in roots:
        p = os.path.join(r, "cache", "raw")
        if p in seen:
            continue
        seen.add(p)
        if os.path.isdir(p):
            out.append(p)
    return out


def db_path() -> str:
    return os.path.join(user_data_dir(), DB_NAME)


def iter_raw() -> list[tuple[str, str, float]]:
    """(计划名, 文件路径, mtime) —— 只要 .txt，跳过 manifest.json。"""
    rows = []
    for root in corpus_roots():
        for plan in sorted(os.listdir(root)):
            pdir = os.path.join(root, plan)
            if not os.path.isdir(pdir):
                continue
            for fn in sorted(os.listdir(pdir)):
                if not fn.lower().endswith(".txt"):
                    continue
                path = os.path.join(pdir, fn)
                try:
                    rows.append((plan, path, os.path.getmtime(path)))
                except OSError:
                    continue
    return rows


def title_of(path: str) -> str:
    """`FM-ST1 胡君_NBT.txt` → `FM-ST1 胡君/NBT`；`年_干员密录_1.txt` → `年/干员密录/1`。"""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _SUFFIX_RE.search(stem)
    if m:
        return stem[:m.start()] + "/" + m.group(1).upper()
    return stem.replace("_", "/")


def doc_body(path: str, meta: dict | None = None) -> str:
    """把一页 wiki 源码还原成可读正文（对白带说话人）。

    meta 非空时回填 speakers：剧情脚本里的 `[name="杜宾"] 台词` 说话人，是现成
    的角色实体词典（编辑一条条标的），比无词典切词干净得多。
    """
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return ""
    try:
        blocks, _ = parse_story(raw)
    except Exception:
        return ""
    parts = []
    speakers: dict[str, int] = {}
    for b in blocks:
        t = b.get("t")
        if isinstance(t, (list, tuple)):  # multiline 对白是一行一段的列表
            t = " ".join(str(x) for x in t)
        t = (t or "").strip()
        if not t:
            continue
        who = (b.get("s") or "").strip()
        if who and b.get("k") == "d":
            speakers[who] = speakers.get(who, 0) + 1
        parts.append(who + "：" + t if who and b.get("k") == "d" else t)
    if meta is not None:
        meta["speakers"] = speakers
    return "\n".join(parts)


# --------------------------------------------------------------------------
# 索引
# --------------------------------------------------------------------------

_SCHEMA = """
create table if not exists docs(
    id     integer primary key,
    title  text,
    name   text,
    code   text,
    plan   text,
    path   text unique,
    mtime  real,
    chars  integer,
    unique(plan, title)
);
create virtual table if not exists docs_fts using fts5(
    body, kw, tokenize='trigram'
);
create table if not exists entities(
    term text primary key,
    df   integer,
    n    integer,
    origin integer
);
create table if not exists doc_entities(
    doc_id integer,
    term   text,
    n      integer,
    primary key(doc_id, term)
);
create table if not exists meta(k text primary key, v text);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path()), exist_ok=True)
    con = sqlite3.connect(db_path(), timeout=15, check_same_thread=False)
    con.execute("pragma journal_mode=wal")
    ver = 0
    try:
        row = con.execute("select v from meta where k='version'").fetchone()
        ver = int(row[0]) if row else 0
    except sqlite3.DatabaseError:
        ver = 0
    if ver and ver != INDEX_VERSION:
        # 结构变了就整库重建，免得读到半旧的行
        for t in ("docs_fts", "doc_entities", "entities", "docs", "meta"):
            con.execute("drop table if exists " + t)
    con.executescript(_SCHEMA)
    return con


def _split_title(title: str) -> tuple[str, str]:
    from .catalog import split_title

    try:
        return split_title(title)
    except Exception:
        return "", title


def candidates_of(text: str) -> dict[str, int]:
    """无词典新词发现：先把虚词替换成分隔符，再统计连续汉字串。返回 {词: 次数}。"""
    out: dict[str, int] = {}
    for m in _CJK_RUN.finditer(_STOP_RE.sub(" ", text)):
        w = m.group(0)
        if w in _STOP_WORDS:
            continue
        out[w] = out.get(w, 0) + 1
    return out


def build(force: bool = False, progress=None) -> dict:
    """增量建索引。progress(done, total, msg)。返回统计。"""
    def say(done, total, msg):
        if progress:
            progress(done, total, msg)

    files = iter_raw()
    total = len(files)
    con = connect()
    if force:
        con.executescript("delete from docs; delete from docs_fts;"
                          "delete from entities; delete from doc_entities;")
        con.commit()

    old = {}
    for row in con.execute("select path, mtime, id from docs"):
        old[row[0]] = (row[1], row[2])

    df: dict[str, int] = {}
    tf: dict[str, int] = {}
    doc_ent: list[tuple[int, str, int]] = []
    link_set: set[str] = set()
    added = skipped = 0

    for i, (plan, path, mtime) in enumerate(files):
        say(i, total, os.path.basename(path))
        prev = old.get(path)
        if prev and not force and abs(prev[0] - mtime) < 1e-6:
            skipped += 1
            doc_id = prev[1]
            # 增量跳过时仍要把实体统计重算进来，否则新页面会拉低已有词的 df
            for term, n in con.execute(
                    "select term, n from doc_entities where doc_id=?", (doc_id,)):
                df[term] = df.get(term, 0) + 1
                tf[term] = tf.get(term, 0) + n
            continue

        meta: dict = {}
        body = doc_body(path, meta)
        if not body:
            continue
        title = title_of(path)
        code, name = _split_title(title)
        cand = candidates_of(body)
        # 说话人是编辑手工标的角色实体（杜宾、整合运动成员…），加权并标记 origin
        for who, n in (meta.get("speakers") or {}).items():
            if len(who) < 2 or who in _STOP_WORDS or who[0] in _STOP_CHARS:
                continue
            link_set.add(who)
            cand[who] = cand.get(who, 0) + n * 3

        if prev:
            doc_id = prev[1]
            con.execute("update docs set title=?,name=?,code=?,plan=?,mtime=?,chars=?"
                        " where id=?", (title, name, code, plan, mtime, len(body), doc_id))
            con.execute("delete from docs_fts where rowid=?", (doc_id,))
            con.execute("delete from doc_entities where doc_id=?", (doc_id,))
        else:
            cur = con.execute(
                "insert into docs(title,name,code,plan,path,mtime,chars)"
                " values(?,?,?,?,?,?,?)",
                (title, name, code, plan, path, mtime, len(body)))
            doc_id = cur.lastrowid
            added += 1

        # 廉价版富化：把本篇的高频专名也塞进 kw 列，让「萨卡兹」能命中
        # 「卡兹戴尔」篇这类语义相邻但字面不含查询词的文档。
        top = sorted(cand.items(), key=lambda kv: -kv[1])[:24]
        kw = " ".join(w for w, _ in top)
        con.execute("insert into docs_fts(rowid, body, kw) values(?,?,?)",
                    (doc_id, body, kw))

        for term, n in cand.items():
            if n < 2 and len(term) < 3:
                continue
            df[term] = df.get(term, 0) + 1
            tf[term] = tf.get(term, 0) + n
            doc_ent.append((doc_id, term, n))

        if i % 40 == 0:
            con.commit()

    con.executemany("insert or replace into doc_entities(doc_id,term,n) values(?,?,?)",
                    doc_ent)
    con.execute("delete from entities")
    keep = [(t, d, tf.get(t, 0), 1 if t in link_set else 0) for t, d in df.items()
            if d >= 2 and tf.get(t, 0) >= 3 and len(t) >= 2]
    # 先保证 wiki 链接实体进表，剩下的名额再给切词候选（切词只做补充）
    keep.sort(key=lambda r: (r[3], r[1]), reverse=True)
    keep = keep[:26000]
    con.executemany("insert or replace into entities(term,df,n,origin) values(?,?,?,?)",
                    keep)
    # 词表之外的候选只留在 doc_entities 里会污染共现树，清掉
    con.execute("delete from doc_entities where term not in (select term from entities)")
    con.execute("insert or replace into meta(k,v) values('built_at',?)",
                (str(int(time.time())),))
    con.execute("insert or replace into meta(k,v) values('version',?)",
                (str(INDEX_VERSION),))
    con.commit()

    stats = {"files": total, "added": added, "skipped": skipped,
             "entities": min(len(keep), 6000), "docs": _count(con)}
    con.close()
    say(total, total, "索引完成：%d 篇" % stats["docs"])
    return stats


def _count(con) -> int:
    return con.execute("select count(*) from docs").fetchone()[0]


def stats() -> dict:
    """不建索引，只报告现状。"""
    if not os.path.isfile(db_path()):
        return {"docs": 0, "chars": 0, "built_at": 0, "entities": 0, "files": len(iter_raw())}
    con = connect()
    try:
        docs, chars = con.execute("select count(*), coalesce(sum(chars),0) from docs").fetchone()
        ent = con.execute("select count(*) from entities").fetchone()[0]
        row = con.execute("select v from meta where k='built_at'").fetchone()
        return {"docs": docs, "chars": chars, "built_at": int(row[0]) if row else 0,
                "entities": ent, "files": len(iter_raw())}
    finally:
        con.close()


# --------------------------------------------------------------------------
# 意图拆解（L1 查询扩展的廉价版：领域词表）
# --------------------------------------------------------------------------

def parse_intent(query: str) -> dict:
    """「萨卡兹的历史发展」→ {core: 萨卡兹, aspects: [历史, 发展], expansions: [...]}"""
    q = (query or "").strip()
    core = q
    for w in _ASK_WORDS:
        core = core.replace(w, "")
    core = re.sub(r"[\s的了吗呢]", "", core)

    aspects, expansions = [], []
    for key, words in _ASPECT_WORDS.items():
        hit = [w for w in words if w in q]
        if not hit:
            continue
        aspects.append(key)
        for w in hit:
            expansions.extend(words)
            core = core.replace(w, "")
    core = re.sub(r"[\s的了吗呢]", "", core)

    seen, ext = set(), []
    for w in expansions:
        if w in core or w in seen or len(w) < 2:
            continue
        seen.add(w)
        ext.append(w)

    if not core:
        # 整句都是问法（如「萨卡兹是怎么来的」），退化成原句里的最长汉字串
        runs = sorted(_CJK_RUN.findall(q), key=len, reverse=True)
        core = runs[0] if runs else q
    return {"query": q, "core": core, "aspects": aspects, "expansions": ext[:12]}


def _match_expr(terms: list[str]) -> str:
    """trigram 短语 OR；短于 3 字的词 trigram 匹配不了，交给 LIKE 兜底。"""
    parts = ['"%s"' % t.replace('"', "") for t in terms if len(t) >= 3]
    return " OR ".join(parts)


# --------------------------------------------------------------------------
# 检索
# --------------------------------------------------------------------------

def _rows(con, expr: str, doc_ids: list[int] | None, limit: int) -> list[tuple]:
    sql = ("select d.id, d.title, d.name, d.code, d.plan, bm25(docs_fts) as sc, "
           "snippet(docs_fts, 0, '\u0001', '\u0002', '…', 48) as snip "
           "from docs_fts join docs d on d.id = docs_fts.rowid "
           "where docs_fts match ? ")
    args: list = [expr]
    if doc_ids:
        sql += " and d.id in (%s) " % ",".join("?" * len(doc_ids))
        args.extend(doc_ids)
    sql += " order by sc limit ?"
    args.append(limit)
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        # 极老的 SQLite 不支持 trigram 或 bm25/snippet：退化为纯 LIKE
        return []


def _like_rows(con, term: str, limit: int) -> list[tuple]:
    try:
        return con.execute(
            "select d.id, d.title, d.name, d.code, d.plan, 0.0 as sc, "
            "substr(docs_fts.body, max(1, instr(docs_fts.body, ?) - 30), 90) as snip "
            "from docs d join docs_fts on docs_fts.rowid = d.id "
            "where docs_fts.body like ? limit ?",
            (term, "%" + term + "%", limit)).fetchall()
    except sqlite3.OperationalError:
        return []


def search_text(query: str, limit: int = 24, extra_terms: list[str] | None = None,
                core_override: str | None = None) -> dict:
    """正文检索。返回 {intent, hits, tree, timeline, corpus}。

    extra_terms 来自概念层（别名 / 共现 / LLM）：查询词本身召回为 0 时
    （「岁家」「岁的历史发展」），靠这些概念词把相关篇目捞出来。
    core_override 让「岁的历史发展」这种只剩一个字的查询仍能算共现树。
    """
    intent = parse_intent(query)
    if core_override:
        intent["core"] = core_override
    core = intent["core"]
    exts = list(intent["expansions"])
    for t in (extra_terms or []):
        if t and t != core and t not in exts and len(t) >= 2:
            exts.append(t)
    intent["expansions"] = exts[:32]
    empty = {"intent": intent, "hits": [], "tree": [], "timeline": [],
             "corpus": stats()}
    st = empty["corpus"]
    if not st["docs"]:
        return empty
    if len(core) < 2:
        # 单字查询 trigram 匹配不了；有概念词就只靠概念词召回，没有就诚实返回空
        if not exts:
            return empty
        core = exts[0]

    con = connect()
    try:
        scores: dict[int, float] = {}
        rows_by_id: dict[int, tuple] = {}

        core_terms = [core]
        expr = _match_expr(core_terms)
        if expr:
            for row in _rows(con, expr, None, max(limit * 4, 60)):
                scores[row[0]] = scores.get(row[0], 0) + (-row[5]) * 3.0
                rows_by_id[row[0]] = row
        else:
            for row in _like_rows(con, core, max(limit * 4, 60)):
                scores[row[0]] = scores.get(row[0], 0) + 0.5
                rows_by_id[row[0]] = row

        if exts:
            expr2 = _match_expr(exts)
            if expr2:
                for row in _rows(con, expr2, None, max(limit * 4, 60)):
                    scores[row[0]] = scores.get(row[0], 0) + (-row[5])
                    rows_by_id.setdefault(row[0], row)
            # 短词（<3字）trigram 召回为 0：重岳/惊蛰/年/夕/令 LIKE 能命中正文，
            # 但 _match_expr 会过滤掉它们，这里单独 LIKE 兜底，否则岁家别名里的
            # 2 字角色名一个都捞不回来（实测重岳25篇/惊蛰20篇 FTS 返回0）。
            # 泛词（年/令/余/岁 命中>40篇）直接跳过：它们是普通汉字，不是角色名
            # 在正文里的命中，召回的全是噪声（主线第7/9/13章就这么混进来的）。
            # 岁家线篇目已通过活动名（洪炉示岁/将进酒…）召回，不缺这几个泛词。
            short_terms = [t for t in exts if len(t) < 3 and t != core]
            for t in short_terms:
                rows = _like_rows(con, t, max(limit * 4, 60))
                if len(rows) > 40:
                    continue  # 泛词：跳过，不召回噪声篇目
                w = 0.4
                for row in rows:
                    if row[0] not in scores:
                        scores[row[0]] = w
                        rows_by_id[row[0]] = row

        # 干员密录/档案命中率高，但不是「剧情发展」：降权后再选 top
        for doc_id in list(scores):
            row = rows_by_id.get(doc_id)
            if row and _KIND_DOWN.search(row[1] or ""):
                scores[doc_id] *= 0.45

        # 共现树用前 200 篇命中统计（只在 top10 上算，噪声太大）
        tree_ids = [d for d, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:200]]

        # 活动代号 → 活动名：前端分组标题显示「洪炉示岁」而非「剧情·AF」
        anames = _act_name_map()
        hits = []
        for doc_id, sc in sorted(scores.items(), key=lambda kv: -kv[1])[:limit]:
            row = rows_by_id[doc_id]
            code = row[3] or ""
            head = ""
            m = _ACT_RE.match(code)
            if m:
                head = m.group(1).upper()
            # 正文里可能有 < >，先转义再插高亮标记，前端才能安全地当 HTML 用
            snip = _esc((row[6] or "").replace("\u0001", "\x00").replace("\u0002", "\x01"),
                        quote=False)
            snip = snip.replace("\x00", "<em>").replace("\x01", "</em>")
            snip = snip.replace("\n", " ").strip()
            hits.append({
                "title": row[1], "name": row[2] or row[1], "code": code,
                "plan": row[4] or "", "score": round(sc, 2), "snippet": snip.strip(),
                "arc": anames.get(head) or "",
            })

        # 按章节路线重排：搜「萨卡兹」给出「10-3 → 10-9 → 11-9」，而不是分数乱序
        hits.sort(key=_route_key)
        return {"intent": intent, "hits": hits,
                "tree": _tree(con, core, 14, tree_ids),
                "timeline": _timeline(hits),
                "corpus": st}
    finally:
        con.close()


def _tree(con, term: str, limit: int, doc_ids: list[int] | None = None) -> list[dict]:
    """和命中篇目同篇共现的实体 —— 「萨卡兹」→ 卡兹戴尔/内战/…

    优先按命中文档聚合：查询词本身未必在 entities 表里（无词典切词会把「维多利亚」
    这类连着别的汉字写在一起的名字整块丢掉），但命中篇目里共同出现的实体仍然能算。
    """
    if not term:
        return []
    if doc_ids:
        try:
            rows = con.execute(
                "select b.term, sum(b.n) as sc, count(*) as docs, e.df, e.origin "
                "from doc_entities b join entities e on e.term = b.term "
                "where b.doc_id in (%s) and b.term <> ? "
                "group by b.term having docs >= 2 "
                "order by docs * 1.0 / (e.df + 8) * (1 + 2 * e.origin) desc, "
                "sc desc limit ?"
                % ",".join("?" * len(doc_ids)),
                [*doc_ids, term, limit * 4]).fetchall()
            out = []
            for r in rows:
                t = r[0]
                if len(t) < 2 or len(t) > 6 or t in _STOP_WORDS:
                    continue
                if t[0] in _STOP_CHARS or t in term or term in t:
                    continue  # 查询词自身的碎片（维多利亚 → 利亚）
                if not r[4] and (len(t) < 3 or r[2] < 3):
                    continue  # 切词候选只在多篇重复出现时才算实体
                out.append({"term": t, "score": int(r[1]), "docs": r[2]})
                if len(out) >= limit:
                    break
            if out:
                return out
        except sqlite3.OperationalError:
            pass
    try:
        rows = con.execute(
            "select b.term, sum(a.n * b.n) as sc, count(*) as docs "
            "from doc_entities a join doc_entities b on b.doc_id = a.doc_id "
            "join entities e on e.term = b.term "
            "where a.term = ? and b.term <> ? "
            "group by b.term order by sc desc limit ?",
            (term, term, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"term": r[0], "score": int(r[1]), "docs": r[2]} for r in rows]


_ACT_RE = re.compile(r"^([A-Za-z]{1,4})[-\d]")
_CH_RE = re.compile(r"^(\d+)-")
# 干员密录/档案是「角色资料」，不是「剧情发展」：搜世界观词时降到后面，
# 否则 top10 会被一堆「XX/干员密录/1」占满（实测 146 篇时就是这个问题）。
_KIND_DOWN = re.compile(r"干员密录|干员档案|/档案/|/spine/")


def _num_after_dash(code: str) -> int:
    m = re.search(r"-(\d+)", code or "")
    return int(m.group(1)) if m else 0


# 活动代号 → 发布时间戳缓存。themes.json 每个活动有 start（CN 服实装时间），
# 用来做发布时间排序：洪炉示岁(2020-01) → 画中人 → 将进酒 … 比代号字母序更接近
# 真实剧情时间线。themes.json 不在（未建主题表）时退化成代号字母序。
# _act_name 同步缓存代号 → 活动简称，供前端分组标题显示「洪炉示岁」而非「AF」。
_act_start: dict[str, int] | None = None
_act_name: dict[str, str] = {}


def _act_start_map() -> dict[str, int]:
    global _act_start, _act_name
    if _act_start is not None:
        return _act_start
    _act_start = {}
    _act_name = {}
    try:
        with open(os.path.join(user_data_dir(), "themes.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
        for a in (data.get("activities") or []):
            head = ""
            for c in (a.get("codes") or []):
                c = str(c).upper()
                if c and not c.isdigit():
                    head = c
                    break
            if head:
                _act_start[head] = int(a.get("start") or 0)
                _act_name[head] = a.get("name") or a.get("title") or head
    except (OSError, ValueError):
        pass
    return _act_start


def _act_name_map() -> dict[str, str]:
    """活动代号 → 活动简称（洪炉示岁/画中人/将进酒 …）。themes.json 不在则空。"""
    if _act_start is None:
        _act_start_map()
    return _act_name


def _route_key(h: dict) -> tuple:
    """阅读顺序：全部按剧情发布时间排（date），同活动/同章节内按 code 序。
    主线不再固定在活动前——主线 EP7(2020-05) 应排在洪炉示岁(2020-01)之后、
    踏寻往昔之风(2020-09)之前。
    组别：0=有日期的活动/主线（按 date 排），1=无日期的活动（date=0，排后面），
    2=密录/档案（最后）。"""
    from . import plot_dates
    title, code = h.get("title") or "", h.get("code") or ""
    if _KIND_DOWN.search(title):
        return (2, 0, 0, 0)
    starts = _act_start_map()
    # 主线 code 如 "7-2"：用 plot_dates.py 的 EP 章节发布日期
    m = _CH_RE.match(code)
    if m:
        ch = int(m.group(1))
        t = plot_dates.ep_date(ch)
        if t:
            return (0, t, ch, _num_after_dash(code))
        return (1, 0, ch, _num_after_dash(code))
    a = _ACT_RE.match(code)
    if a:
        t = starts.get(a.group(1).upper(), 0)
        if t:
            return (0, t, sum(ord(c) for c in a.group(1)), _num_after_dash(code))
        # 无日期：退回字母序，但排到组 1（有日期活动之后、密录之前）
        return (1, 0, sum(ord(c) for c in a.group(1)), _num_after_dash(code))
    return (1, 0, 0, 0)



def _timeline(hits: list[dict]) -> list[dict]:
    """按活动代号把命中篇目排成一条线，方便「该按什么顺序看」。"""
    out = []
    for h in hits:
        code = h["code"]
        m = _ACT_RE.match(code)
        out.append({"title": h["title"], "name": h["name"],
                    "code": code, "act": m.group(1).upper() if m else ""})
    out.sort(key=_route_key)
    return out
