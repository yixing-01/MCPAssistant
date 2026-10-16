"""个人隐私目录及其后代路径深度防护测试"""

import os
import pytest
from src.catalog.scanner import SoftwareScanner, is_path_within_protected_root


def test_protected_root_itself_blocked():
    """受保护目录本身（如 Documents、Downloads）必须被阻止"""
    docs = os.path.expandvars(r"%USERPROFILE%\Documents")
    downloads = os.path.expandvars(r"%USERPROFILE%\Downloads")

    assert is_path_within_protected_root(docs) is True
    assert is_path_within_protected_root(downloads) is True


def test_descendants_of_protected_root_blocked():
    """受保护目录的任意层级子目录（Documents\\Sub, Downloads\\A\\B）必须被阻止"""
    docs_sub = os.path.expandvars(r"%USERPROFILE%\Documents\MyApp")
    docs_deep = os.path.expandvars(r"%USERPROFILE%\Documents\Vendor\Nested\App")
    downloads_sub = os.path.expandvars(r"%USERPROFILE%\Downloads\PortableApp")

    assert is_path_within_protected_root(docs_sub) is True
    assert is_path_within_protected_root(docs_deep) is True
    assert is_path_within_protected_root(downloads_sub) is True


def test_similar_prefix_sibling_not_misidentified():
    """名称前缀相似的同级目录（如 DocumentsBackup）绝不能被误判为受保护目录的后代"""
    sibling = os.path.expandvars(r"%USERPROFILE%\DocumentsBackup")
    sibling2 = os.path.expandvars(r"%USERPROFILE%\Downloads_Old")

    assert is_path_within_protected_root(sibling) is False
    assert is_path_within_protected_root(sibling2) is False


def test_localappdata_programs_allowed():
    """%LOCALAPPDATA%\\Programs 是合法的软件安装位置，绝不能被整体误拦截"""
    programs_dir = os.path.expandvars(r"%LOCALAPPDATA%\Programs")
    vscode_dir = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code")

    assert is_path_within_protected_root(programs_dir) is False
    assert is_path_within_protected_root(vscode_dir) is False


def test_drive_root_blocked():
    """盘符根目录（C:\\, D:\\）必须被阻止"""
    assert is_path_within_protected_root("C:\\") is True
    assert is_path_within_protected_root("D:\\") is True
    assert is_path_within_protected_root("C:/") is True


def test_different_drive_does_not_crash():
    """跨不同磁盘（如 C: 与 D:）比较路径时不应抛出未捕获异常"""
    assert is_path_within_protected_root("D:\\MyVendor\\Tool") is False
    assert is_path_within_protected_root("E:\\Games\\App") is False
