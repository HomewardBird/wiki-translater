# -*- coding: utf-8 -*-
"""PyInstaller 打包入口：只启动图形界面（--windowed 版专用，不弹控制台）。

没有控制台时异常会被吞掉，所以崩溃时把 traceback 写到 exe 同目录的 gui-error.log。
"""
import os
import sys
import traceback

from wiki_transporter.gui import run_gui


def _log_crash() -> None:
    try:
        here = os.path.dirname(os.path.abspath(sys.executable))
        with open(os.path.join(here, "gui-error.log"), "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
    except Exception:
        pass


if __name__ == "__main__":
    try:
        sys.exit(run_gui())
    except SystemExit:
        raise
    except BaseException:
        _log_crash()
        raise
