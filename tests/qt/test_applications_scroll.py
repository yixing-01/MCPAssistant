"""“我的应用”列表授权开关滚动锚点回归测试。

验证：
1. 在长列表中关闭单项授权（已允许 -> 未允许），目标应用从“已允许”消失并在“未允许”中出现，当前视口保持在点击前位置附近，绝不跳到列表底部。
2. 在长列表中开启单项授权（未允许 -> 已允许），目标应用从“未允许”消失并在“已允许”中出现，视口保持在原位置附近。
3. 列表接近底部但不是最底端时切换开关，滚动位置不被错误吸附到新 maximum。
4. 主动切换标签页或输入搜索词时，不继承上一次单项切换的滚动位置。
"""

from __future__ import annotations

import time
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import sys
from src.catalog.models import AppEntry, LaunchType
from src.catalog.manager import CatalogManager
from src.core.config import ConfigManager
from src.ui_qt.controllers.applications_controller import ApplicationsController
from src.ui_qt.views.applications_page import ApplicationsPage
from src.ui_qt.presentation.apps import AppBucket


def _create_test_page(count: int = 60, all_enabled: bool = True) -> tuple[ApplicationsPage, ApplicationsController, CatalogManager]:
    cat = CatalogManager()
    cat.apps = {
        f"app_{i:03d}": AppEntry(
            app_id=f"app_{i:03d}",
            display_name=f"测试应用_{i:03d}",
            source="test",
            is_enabled=all_enabled,
            launch_type=LaunchType.WIN32,
            exe_path=sys.executable,
        )
        for i in range(count)
    }
    cfg = ConfigManager()
    ctrl = ApplicationsController(cat, cfg)
    page = ApplicationsPage(ctrl)
    page.resize(1000, 600)
    return page, ctrl, cat


def test_toggle_allowed_to_not_allowed_preserves_scroll(qtbot):
    """1. 已允许 -> 未允许：目标项迁移，视口保持在点击前附近，绝不跳底。"""
    page, ctrl, cat = _create_test_page(count=60, all_enabled=True)
    qtbot.addWidget(page)
    page.show()
    qtbot.waitUntil(lambda: len(page.rows) == 60 and page.scroll.verticalScrollBar().maximum() > 1000)

    vbar = page.scroll.verticalScrollBar()
    # 滚动到列表中部
    target_pos = 1500
    vbar.setValue(target_pos)
    qtbot.wait(50)
    before_val = vbar.value()
    assert before_val > 1000

    # 选取当前视口内可见的应用行（例如第 25 行）
    target_row = page.rows[25]
    target_id = target_row.item.app_id

    # 点击开关切换授权状态
    qtbot.mouseClick(target_row.enabled_toggle, Qt.MouseButton.LeftButton)

    # 等待控制器发布并重建列表
    qtbot.waitUntil(lambda: target_id not in [r.item.app_id for r in page.rows], timeout=3000)
    qtbot.wait(100)

    # 断言：已从当前分组移除
    assert target_id not in [r.item.app_id for r in page.rows]
    assert len(page.rows) == 59

    # 断言：滚动条绝没有跳到最底部
    new_max = vbar.maximum()
    assert vbar.value() != new_max, f"滚动条错误跳到了最底部！当前值={vbar.value()}, 最大值={new_max}"
    # 断言：滚动位置在合理容差内保持（单行高度约 50~65px）
    assert abs(vbar.value() - before_val) <= 120, f"滚动位置偏差过大: 之前={before_val}, 当前={vbar.value()}"

    # 断言：切换到“未允许”分组能够看到目标项
    page.bucket_buttons[1].click()
    qtbot.waitUntil(lambda: target_id in [r.item.app_id for r in page.rows], timeout=2000)
    assert any(r.item.app_id == target_id for r in page.rows)


def test_toggle_not_allowed_to_allowed_preserves_scroll(qtbot):
    """2. 未允许 -> 已允许：目标项迁移，视口保持在点击前附近，绝不跳底。"""
    page, ctrl, cat = _create_test_page(count=60, all_enabled=False)
    qtbot.addWidget(page)
    page.show()

    # 切换到未允许标签
    page.bucket_buttons[1].click()
    qtbot.waitUntil(lambda: len(page.rows) == 60 and page.scroll.verticalScrollBar().maximum() > 1000)

    vbar = page.scroll.verticalScrollBar()
    target_pos = 1500
    vbar.setValue(target_pos)
    qtbot.wait(50)
    before_val = vbar.value()

    target_row = page.rows[25]
    target_id = target_row.item.app_id

    qtbot.mouseClick(target_row.enabled_toggle, Qt.MouseButton.LeftButton)

    qtbot.waitUntil(lambda: target_id not in [r.item.app_id for r in page.rows], timeout=3000)
    qtbot.wait(100)

    assert target_id not in [r.item.app_id for r in page.rows]
    assert len(page.rows) == 59

    new_max = vbar.maximum()
    assert vbar.value() != new_max, f"滚动条错误跳到了最底部！当前值={vbar.value()}, 最大值={new_max}"
    assert abs(vbar.value() - before_val) <= 120

    # 切换到已允许分组能够找到该项
    page.bucket_buttons[0].click()
    qtbot.waitUntil(lambda: target_id in [r.item.app_id for r in page.rows], timeout=2000)
    assert any(r.item.app_id == target_id for r in page.rows)


def test_toggle_near_bottom_does_not_snap_to_maximum(qtbot):
    """3. 列表接近底部但不是最底端时切换开关，不被错误吸附到新 maximum。"""
    page, ctrl, cat = _create_test_page(count=60, all_enabled=True)
    qtbot.addWidget(page)
    page.show()
    qtbot.waitUntil(lambda: len(page.rows) == 60 and page.scroll.verticalScrollBar().maximum() > 1000)

    vbar = page.scroll.verticalScrollBar()
    max_val = vbar.maximum()
    # 距离底部 250px（约 4 行的距离），明确不是最底端
    near_bottom = max_val - 250
    vbar.setValue(near_bottom)
    qtbot.wait(50)
    before_val = vbar.value()

    # 寻找当前可见区域内的行
    target_row = page.rows[55]
    target_id = target_row.item.app_id

    qtbot.mouseClick(target_row.enabled_toggle, Qt.MouseButton.LeftButton)

    qtbot.waitUntil(lambda: target_id not in [r.item.app_id for r in page.rows], timeout=3000)
    qtbot.wait(100)

    new_max = vbar.maximum()
    assert vbar.value() < new_max, f"滚动条被错误吸附到了最底端！当前={vbar.value()}, 最大={new_max}"
    assert abs(vbar.value() - before_val) <= 120


def test_tab_switch_and_search_do_not_restore_stale_scroll(qtbot):
    """4. 用户主动切换标签或搜索时，不错误恢复上一个分组的滚动位置。"""
    page, ctrl, cat = _create_test_page(count=60, all_enabled=True)
    qtbot.addWidget(page)
    page.show()
    qtbot.waitUntil(lambda: len(page.rows) == 60 and page.scroll.verticalScrollBar().maximum() > 1000)

    vbar = page.scroll.verticalScrollBar()
    vbar.setValue(1500)
    qtbot.wait(50)

    # 主动切换到“未允许”分组
    page.bucket_buttons[1].click()
    qtbot.wait(100)
    # 未允许分组初始应处于顶部 (0)
    assert vbar.value() == 0

    # 切换回已允许分组
    page.bucket_buttons[0].click()
    qtbot.wait(100)
    vbar.setValue(1500)
    qtbot.wait(50)

    # 触发搜索
    page.search.setText("测试应用_001")
    qtbot.wait(100)
    # 搜索结果列表应处于顶部 (0)
    assert vbar.value() == 0
