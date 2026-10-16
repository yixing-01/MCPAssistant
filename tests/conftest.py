"""全局 Pytest Fixture 配置与测试资源隔离保护"""

import gc
import pytest


@pytest.fixture(autouse=True)
def clean_memory():
    """每次测试运行结束后进行垃圾回收，防止资源泄漏。"""
    yield
    gc.collect()
