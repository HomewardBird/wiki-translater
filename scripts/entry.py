# -*- coding: utf-8 -*-
"""PyInstaller 打包入口（单独成文件，避免相对导入在脚本模式下失效）。"""
import sys

from wiki_transporter.cli import main

if __name__ == "__main__":
    sys.exit(main())
