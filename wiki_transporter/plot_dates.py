# -*- coding: utf-8 -*-
"""主线章节发布日期表。来源：ArkPlots Plotline.json（CN 服实装日期）。
活动代号（AF/BI/IW...）的日期在 themes.json 里，这里只补主线 EP0-EP17。
themes.json 的主线 start=0，导致 _route_key 把主线排到活动前。"""
from datetime import datetime

# 主线章节号 → CN 服实装日期
_EP_DATE_STR = {
    0: "2019-05-01", 1: "2019-05-01", 2: "2019-05-01",
    3: "2019-05-01", 4: "2019-05-01", 5: "2019-07-09",
    6: "2019-12-24", 7: "2020-05-01", 8: "2020-11-01",
    9: "2021-09-17", 10: "2022-04-14", 11: "2022-10-11",
    12: "2023-04-06", 13: "2023-10-08", 14: "2024-05-01",
    15: "2025-04-07", 16: "2025-10-09", 17: "2026-05-01",
}

_cache: dict[int, int] = {}


def ep_date(ch: int) -> int:
    """主线章节号 → 发布时间戳。无映射返回 0。"""
    if ch not in _cache:
        ds = _EP_DATE_STR.get(ch)
        _cache[ch] = int(datetime.strptime(ds, '%Y-%m-%d').timestamp()) if ds else 0
    return _cache[ch]
