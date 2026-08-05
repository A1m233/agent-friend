"""Collect Trafilatura configuration and schema resources used at runtime."""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("trafilatura")
