"""Collect jusText language stoplists used by Trafilatura at runtime."""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("justext")
