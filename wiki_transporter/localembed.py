# -*- coding: utf-8 -*-
"""本地向量模型（bge-small-zh-v1.5，ONNX INT8）：下载、缓存、编码。

为什么单独一个模块：语义搜索要一个「把文字变成向量」的模型，走 API 要联网、
要 key、还要把剧情全文发给对方。这里改成纯本地：模型文件放在
`<用户目录>/models/bge-small-zh-v1.5/`，用 onnxruntime 跑 CPU 推理。

- 模型不随安装包分发，用户点了「下载模型」才下（约 24MB，两个文件）；
- 没下载 / 下载不完整时 `ready()` 为假，`llm.config()` 的 embed_model 保持空，
  整条语义检索链路自动跳过，不会把搜索拖坏；
- 下载走 `net.safe_get`（项目唯一的出站入口，带 SSRF 校验），先落 `.part`
  再原子改名，下到一半断网不会留下半个模型。

文件来源：Xenova/bge-small-zh-v1.5（HuggingFace 上给 transformers.js 用的
ONNX 版本，和 BAAI 的 bge-small-zh-v1.5 权重一致）。
"""
from __future__ import annotations

import os

NAME = "bge-small-zh-v1.5"
LABEL = "bge-small-zh-v1.5（本地）"
DIM = 512

# (远端路径, 本地文件名, 大致字节数)
_FILES = (
    ("onnx/model_quantized.onnx", "model.onnx", 24 * 1024 * 1024),
    ("tokenizer.json", "tokenizer.json", 440 * 1024),
)
_REPO = "Xenova/bge-small-zh-v1.5"
_HOSTS = ("hf-mirror.com", "huggingface.co")   # 先镜像后源站
# 下载会被重定向到 CDN：hf-mirror → huggingface.co → us.aws.cdn.hf.co，
# 所以出站白名单要连重定向目标一起放行（通配 *.hf.co 覆盖各区域 CDN）。
_ALLOWED = ["hf-mirror.com", "huggingface.co", "*.huggingface.co", "hf.co", "*.hf.co"]
_MAX_BYTES = 64 * 1024 * 1024


def model_dir() -> str:
    from .config import user_data_dir

    return os.path.join(user_data_dir(), "models", NAME)


def _paths() -> dict[str, str]:
    d = model_dir()
    return {local: os.path.join(d, local) for _, local, _ in _FILES}


def ready() -> bool:
    """模型文件齐了且体积不离谱才算可用。"""
    for _, local, size in _FILES:
        p = _paths()[local]
        if not os.path.isfile(p) or os.path.getsize(p) < size * 0.5:
            return False
    return True


def deps_ready() -> bool:
    """onnxruntime + tokenizers 都在才能编码（打包版可能没带上）。"""
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401

        return True
    except Exception:
        return False


def status() -> dict:
    got = 0
    for _, local, _ in _FILES:
        p = _paths()[local]
        if os.path.isfile(p):
            got += os.path.getsize(p)
    total = sum(sz for _, _, sz in _FILES)
    return {"name": NAME, "label": LABEL, "ready": ready(), "dir": model_dir(),
            "deps": deps_ready(), "bytes": got,
            "size_mb": round(total / 1048576, 1)}


def remove() -> dict:
    for p in _paths().values():
        try:
            os.remove(p)
        except OSError:
            pass
    _SESSION.clear()
    return {"ok": True, "ready": ready()}


def download(progress=None) -> dict:
    """下载模型文件。progress(i, n, 说明文字)。"""
    from .net import NetError, safe_get

    d = model_dir()
    os.makedirs(d, exist_ok=True)
    n = len(_FILES)
    for i, (remote, local, size) in enumerate(_FILES):
        target = os.path.join(d, local)
        if os.path.isfile(target) and os.path.getsize(target) >= size * 0.5:
            if progress:
                progress(i + 1, n, "%s 已存在，跳过" % local)
            continue
        if progress:
            progress(i, n, "下载 %s（约 %.0fMB）" % (local, size / 1048576))
        err: Exception | None = None
        for host in _HOSTS:
            url = "https://%s/%s/resolve/main/%s" % (host, _REPO, remote)
            try:
                r = safe_get(url, allowed_hosts=_ALLOWED, timeout=180,
                             max_bytes=_MAX_BYTES)
            except (NetError, OSError, ValueError) as e:
                err = e
                continue
            blob = r.content or b""
            if len(blob) < size * 0.5:
                err = ValueError("%s 只返回 %d 字节" % (host, len(blob)))
                continue
            tmp = target + ".part"
            with open(tmp, "wb") as f:
                f.write(blob)
            os.replace(tmp, target)
            err = None
            break
        if err is not None:
            return {"ok": False, "error": "下载 %s 失败：%s" % (local, err)}
        if progress:
            progress(i + 1, n, "%s 完成" % local)
    # 下完就丢掉旧的编码器，避免继续用旧图
    _SESSION.clear()
    return {"ok": True, "dir": d, "ready": ready()}


# ---------------------------------------------------------------- 编码
_SESSION: dict = {}


def _session():
    """返回 (onnx 会话, tokenizer)；第一次调用时加载，之后复用。"""
    if _SESSION.get("sess") is not None:
        return _SESSION["sess"], _SESSION["tok"]
    import onnxruntime as ort
    from tokenizers import Tokenizer

    d = model_dir()
    tok = Tokenizer.from_file(os.path.join(d, "tokenizer.json"))
    tok.enable_truncation(max_length=512)
    tok.enable_padding(pad_id=0, pad_token="[PAD]")
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, min(8, (os.cpu_count() or 4) // 2))
    opts.log_severity_level = 3
    sess = ort.InferenceSession(os.path.join(d, "model.onnx"), opts,
                                providers=["CPUExecutionProvider"])
    _SESSION.update(sess=sess, tok=tok)
    return sess, tok


def embed(texts: list[str], batch: int = 8) -> list[list[float]] | None:
    """把一批文本编码成归一化向量（余弦相似度=点积）。失败返回 None。"""
    if not texts or not ready():
        return None
    try:
        import numpy as np

        sess, tok = _session()
        names = {i.name for i in sess.get_inputs()}
        out: list[list[float]] = []
        for i in range(0, len(texts), batch):
            chunk = [(t or "")[:2000] or " " for t in texts[i:i + batch]]
            encs = tok.encode_batch(chunk)
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in names:
                feeds["token_type_ids"] = np.zeros_like(ids)
            hidden = sess.run(None, feeds)[0]
            m = mask[..., None].astype(np.float32)
            vec = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
            vec = vec / np.clip(np.linalg.norm(vec, axis=1, keepdims=True), 1e-9, None)
            out.extend(vec.tolist())
        return out
    except Exception:
        return None
