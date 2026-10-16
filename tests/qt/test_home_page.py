import random
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from src.ui_qt.presentation.apps import AppBucket, AppListItem
from src.ui_qt.state import ProductStatus, ProductStatusKind
from src.ui_qt.views.home_page import AppShortcutCard, HomePage


def _make_item(app_id: str, name: str, bucket: AppBucket = AppBucket.ALLOWED, voice: str = "") -> AppListItem:
    return AppListItem(
        app_id=app_id,
        display_name=name,
        aliases=(name,),
        category="电脑应用",
        icon_ref=f"C:\\apps\\{app_id}.exe",
        is_enabled=(bucket == AppBucket.ALLOWED),
        bucket=bucket,
        voice_example=voice or f"打开{name}",
        launch_type="win32",
    )


def test_ready_home_uses_novice_copy(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.set_product_status(ProductStatus(
        ProductStatusKind.READY,
        "MCP助手已准备好",
        "服务已在后台就绪，可通过 MCP 调起已允许的应用。",
        "试着打开一个应用",
    ))
    assert page.status_title.text() == "MCP助手已准备好"
    assert page.primary_button.text() == "试着打开一个应用"
    assert page.primary_button.isVisibleTo(page)


def test_recovery_action_emits_retry(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.set_product_status(ProductStatus(
        ProductStatusKind.RECOVERING,
        "MCP 服务暂时无法连接",
        "正在自动重新连接。",
        "立即重试",
        8,
    ))
    with qtbot.waitSignal(page.retry_requested, timeout=1000):
        qtbot.mouseClick(page.primary_button, Qt.MouseButton.LeftButton)
    assert "8 秒" in page.retry_label.text()


def test_home_contains_no_developer_terms(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    text = " ".join(label.text() for label in page.findChildren(type(page.status_title)))
    for term in ("WebSocket", "Endpoint", "DPAPI", "JSON-RPC"):
        assert term not in text


def test_allowed_apps_candidate_filtering_and_deduplication(qtbot):
    """验证首页仅接收 AppBucket.ALLOWED 候选且按 app_id 去重。"""
    page = HomePage()
    qtbot.addWidget(page)

    raw_items = [
        _make_item("app_1", "应用一", AppBucket.ALLOWED),
        _make_item("app_1", "应用一重复", AppBucket.ALLOWED),  # 重复ID
        _make_item("app_2", "应用二", AppBucket.NOT_ALLOWED),  # 未允许
        _make_item("app_3", "应用三", AppBucket.NEEDS_ATTENTION),  # 需关注
        _make_item("app_4", "应用四", AppBucket.ALLOWED),
    ]

    page.set_allowed_apps(raw_items)
    # 应仅有 app_1 与 app_4 两个有效候选
    assert len(page._app_cards) == 2
    card_names = [card.item.display_name for card in page._app_cards]
    assert "应用一" in card_names
    assert "应用四" in card_names
    assert "应用二" not in card_names
    assert "应用三" not in card_names


def test_allowed_apps_sampling_more_than_three(qtbot):
    """验证大于 3 个已允许应用时，随机抽取 3 个，支持可预测随机发生器。"""
    rng = random.Random(12345)
    page = HomePage(random_generator=rng)
    qtbot.addWidget(page)

    candidates = [_make_item(f"app_{i}", f"应用{i}") for i in range(1, 7)]
    page.set_allowed_apps(candidates)

    assert len(page._app_cards) == 3
    # 验证抽取的 3 个卡片均来自候选
    shown_ids = {card.app_id for card in page._app_cards}
    assert len(shown_ids) == 3
    assert shown_ids.issubset({c.app_id for c in candidates})


def test_allowed_apps_sampling_less_than_three_preserves_width(qtbot):
    """验证少于 3 个卡片时保持固定宽度 260px，不被不自然拉伸。"""
    page = HomePage()
    qtbot.addWidget(page)

    # 1 个应用
    page.set_allowed_apps([_make_item("app_1", "应用一")])
    assert len(page._app_cards) == 1
    assert page._app_cards[0].maximumWidth() == 260 or page._app_cards[0].width() <= 260

    # 2 个应用
    page.set_allowed_apps([_make_item("app_1", "应用一"), _make_item("app_2", "应用二")])
    assert len(page._app_cards) == 2
    assert page._app_cards[0].maximumWidth() == 260 or page._app_cards[0].width() <= 260
    assert page._app_cards[1].maximumWidth() == 260 or page._app_cards[1].width() <= 260


def test_allowed_apps_empty_state_and_navigation(qtbot):
    """验证 0 个已允许应用时展示新手友好空状态，且点击设置按钮触发跳转信号。"""
    page = HomePage()
    qtbot.addWidget(page)

    page.set_allowed_apps([])
    assert len(page._app_cards) == 0

    # 验证空状态卡片与文案存在
    empty_card = page.findChild(type(page.status_card), "homeEmptyCard")
    assert empty_card is not None
    text = " ".join(lbl.text() for lbl in empty_card.findChildren(type(page.status_title)))
    assert "还没有允许小智打开和关闭的应用" in text

    # 验证点击设置按钮发出 open_apps_requested
    setting_btn = empty_card.findChild(QPushButton)
    assert setting_btn is not None
    assert setting_btn.text() == "去我的应用设置"
    with qtbot.waitSignal(page.open_apps_requested, timeout=1000):
        qtbot.mouseClick(setting_btn, Qt.MouseButton.LeftButton)


def test_session_stability_preserves_selection(qtbot):
    """验证会话内稳定：状态改变、切页或传入相同集合时不重新随机抽样。"""
    rng = random.Random(999)
    page = HomePage(random_generator=rng)
    qtbot.addWidget(page)

    candidates = [_make_item(f"app_{i}", f"软件{i}") for i in range(1, 10)]
    page.set_allowed_apps(candidates)
    initial_ids = [card.app_id for card in page._app_cards]
    assert len(initial_ids) == 3

    # 1. 改变产品连接状态，不应重新抽样
    page.set_product_status(ProductStatus(ProductStatusKind.READY, "新状态", "说明"))
    assert [card.app_id for card in page._app_cards] == initial_ids

    # 2. 重新传入相同应用集合（即使顺序颠倒），不应重新抽样
    reversed_candidates = list(reversed(candidates))
    page.set_allowed_apps(reversed_candidates)
    assert [card.app_id for card in page._app_cards] == initial_ids

    # 3. 当集合内容实质变化（如新增了一个应用），才会重新抽样
    candidates_with_new = candidates + [_make_item("app_new", "新软件")]
    page.set_allowed_apps(candidates_with_new)
    new_ids = [card.app_id for card in page._app_cards]
    assert len(new_ids) == 3


def test_app_shortcut_card_rendering_and_click(qtbot):
    """验证快捷卡片正确渲染名称、语音文案、中性承托底及点击事件。"""
    page = HomePage()
    qtbot.addWidget(page)

    item = _make_item("app_wx", "微信", voice="可以说：打开微信")
    card = AppShortcutCard(item)
    qtbot.addWidget(card)

    assert card.icon_label.objectName() == "homeCardIconContainer"
    assert card.findChild(type(page.status_title), "itemTitle").text() == "微信"
    assert "打开微信" in card.findChild(type(page.status_title), "subText").text()

    # 点击卡片触发 clicked 信号
    with qtbot.waitSignal(card.clicked, timeout=1000):
        qtbot.mouseClick(card, Qt.MouseButton.LeftButton)
