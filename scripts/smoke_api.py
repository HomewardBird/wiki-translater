# -*- coding: utf-8 -*-
"""对运行中的 GUI 服务做一遍接口冒烟测试（开发用）。"""
import json
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8765"


def token() -> str:
    with urllib.request.urlopen(BASE + "/", timeout=10) as r:
        html = r.read().decode("utf-8")
    i = html.find('name="token" content="')
    return html[i + len('name="token" content="'):].split('"')[0]


TOK = token()


def call(path, body=None, timeout=60):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={"X-Token": TOK})
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def search(source, q):
    d = call("/api/search", {"source": source, "q": q})
    print("==", q, "|", d.get("mode"), d.get("total"),
          "| dym:", (d.get("did_you_mean") or {}).get("name"))
    for r in d.get("results", [])[:5]:
        print("    ", r["score"], r["name"], "|", r["title"])
    return d


def follow(job_id, quiet=True):
    while True:
        d = call("/api/job/" + job_id)
        job = d["job"]
        if not quiet and job["log"]:
            print("   ", job["log"][-1]["m"], "%.0f%%" % (job["progress"] * 100))
        if job["status"] != "running":
            return job
        time.sleep(0.4)


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "ys-wiki"
    for q in sys.argv[2:]:
        search(src, q)
