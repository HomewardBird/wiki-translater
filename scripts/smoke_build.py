# -*- coding: utf-8 -*-
"""对运行中的 GUI 服务跑一次「搜索 → 预览 → 生成」，用于开发验证。"""
import sys
import time

from smoke_api import call, follow, search


def build(source: str, titles: list[str], title: str = "", formats=("pdf",)) -> dict:
    prev = call("/api/preview", {"source": source, "titles": titles, "title": title})
    plan = prev["plan"]
    print("plan:", plan["id"], "|", plan["title"], "| pages", plan["pages"],
          "| cached", plan["cached"])
    for vol in plan["volumes"]:
        print("  vol:", vol["title"])
        for sec in vol["sections"]:
            print("     sec:", sec["name"], sec["pages"])
    started = time.time()
    j = call("/api/build", {"plan_id": plan["id"], "formats": list(formats)})
    job = follow(j["job"], quiet=False)
    print("status:", job["status"], "| %.1fs" % (time.time() - started), "|", job["error"])
    for f in job["result"].get("files", []):
        print("   ", f["kind"], f["name"], f["size"])
    return job


if __name__ == "__main__":
    src = sys.argv[1]
    q = sys.argv[2]
    res = search(src, q)
    titles = [r["title"] for r in res["results"][:3]]
    build(src, titles, title=q)
