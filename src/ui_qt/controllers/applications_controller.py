from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal, Slot

from src.ui_qt.presentation.apps import AppBucket, AppListItem, build_app_items, filter_items


class ApplicationsController(QObject):
    """应用页面业务控制器，管理搜索、筛选、单项授权切换、安全添加、手动应用修复与后台刷新。"""

    items_changed = Signal(list)
    counts_changed = Signal(dict)
    allowed_items_changed = Signal(list)
    busy_changed = Signal(bool)
    user_error = Signal(str)
    _scan_finished = Signal(int, object)
    _batch_finished = Signal(int, object)

    def __init__(self, catalog_manager, config_manager, parent=None):
        super().__init__(parent)
        self.catalog_manager = catalog_manager
        self.config_manager = config_manager
        self._items = []
        self._query = ""
        self._bucket = AppBucket.ALLOWED
        self._scan_request_id = 0
        self._batch_request_id = 0
        self._visible_ids = []
        self._is_busy = False
        self._op_lock = threading.Lock()
        self._scan_finished.connect(self._accept_scan, Qt.ConnectionType.QueuedConnection)
        self._batch_finished.connect(self._accept_batch, Qt.ConnectionType.QueuedConnection)
        self.reload()

    @property
    def is_busy(self) -> bool:
        """返回控制器当前是否正在执行后台异步任务。"""
        return self._is_busy

    def reload(self) -> None:
        """从底层的 CatalogManager 重新加载全量应用并构建展示项。"""
        self._items = build_app_items(self.catalog_manager.get_all_apps())
        self._publish()

    @property
    def all_catalog_ids(self) -> set[str]:
        """返回当前控制器已知的所有展示条目的 ID 集合。"""
        return {item.app_id for item in self._items}

    # 别名兼容
    all_app_ids = all_catalog_ids

    def allowed_items(self) -> list[AppListItem]:
        """返回当前完整的已允许应用展示列表（不受“我的应用”页面搜索与筛选标签影响）。"""
        return [item for item in self._items if item.bucket is AppBucket.ALLOWED]

    def _publish(self) -> None:
        """发布当前分组各统计数，并根据查询词与分组过滤可见项后发射变更信号。"""
        counts = {
            bucket.value: len([item for item in self._items if item.bucket is bucket])
            for bucket in AppBucket
        }
        self.counts_changed.emit(counts)
        self.allowed_items_changed.emit(self.allowed_items())
        visible = filter_items(self._items, self._query, self._bucket)
        self._visible_items = visible
        self._visible_ids = [item.app_id for item in visible]
        self.items_changed.emit(visible)

    def visible_toggleable_count(self) -> int:
        """返回当前筛选可见条目中可切换授权的数量。"""
        items = getattr(self, "_visible_items", [])
        return sum(1 for item in items if getattr(item, "can_toggle", True))

    def set_query(self, query: str) -> None:
        """更新搜索关键词并重新计算展示列表。"""
        self._query = query
        self._publish()

    def set_bucket(self, bucket: AppBucket) -> None:
        """切换当前激活的分组标签（已允许/未允许/需要处理）。"""
        self._bucket = bucket
        self._publish()

    def set_enabled(self, app_id: str, enabled: bool) -> None:
        """单项应用直接切换授权开关，并捕获校验异常。"""
        app = None
        if hasattr(self.catalog_manager, "apps") and isinstance(self.catalog_manager.apps, dict):
            app = self.catalog_manager.apps.get(app_id)
        if app is None and hasattr(self.catalog_manager, "get_all_apps"):
            for a in self.catalog_manager.get_all_apps():
                if getattr(a, "app_id", None) == app_id:
                    app = a
                    break

        # 待处理条目在修复前禁止切换授权开关，直接忽略
        if app and not getattr(app, "is_valid", True):
            if getattr(app, "is_user_managed", False) or getattr(app, "requires_user_attention", False):
                return

        if not self.catalog_manager.set_app_enabled(app_id, enabled):
            # 授权失败时，若条目非高危/受限，设置 requires_user_attention=True，并切换至“需要处理”分组
            if app is not None and not (getattr(app, "is_restricted", False) or getattr(app, "validation_status", "") == "dangerous"):
                app.requires_user_attention = True
            self._bucket = AppBucket.NEEDS_ATTENTION
            self.reload()
            self.user_error.emit("这个扫描入口不能直接允许，已移到“需要处理”。请选择软件主程序。")
            return
        self.reload()

    def batch_set_visible(self, enabled: bool) -> None:
        """对当前明确可见、明确筛选出的应用 ID 执行批量授权或撤销（后台异步执行，不卡死 Qt 主线程）。"""
        with self._op_lock:
            if self._is_busy or not self._visible_ids:
                return

            # 严格过滤出当前可见且允许切换授权的条目 ID (can_toggle=True)
            # 待处理条目 (NEEDS_ATTENTION) can_toggle 为 False，绝不参与批量操作
            visible_items = getattr(self, "_visible_items", None)
            if visible_items is not None:
                toggleable_map = {item.app_id: getattr(item, "can_toggle", True) for item in visible_items}
            else:
                toggleable_map = {item.app_id: getattr(item, "can_toggle", True) for item in self._items}

            target_ids = [aid for aid in self._visible_ids if toggleable_map.get(aid, True)]
            if not target_ids:
                return

            self._is_busy = True
            self._batch_request_id += 1
            req_id = self._batch_request_id

        self.busy_changed.emit(True)

        def _worker():
            try:
                res = self.catalog_manager.batch_set_enabled(
                    enabled,
                    app_ids=target_ids,
                )
            except Exception as exc:
                res = exc
            self._batch_finished.emit(req_id, res)

        threading.Thread(target=_worker, name="CatalogBatchWorker", daemon=True).start()

    @Slot(int, object)
    def _accept_batch(self, req_id: int, result: object) -> None:
        """接收后台批量操作完成事件，过滤过期请求并原子发布新数据。"""
        with self._op_lock:
            if req_id != self._batch_request_id:
                return
            self._is_busy = False

        self.busy_changed.emit(False)
        if isinstance(result, Exception):
            self.user_error.emit("批量更新应用授权失败，请稍后重试。")
        self.reload()

    # 别名兼容
    set_visible_enabled = batch_set_visible

    def add_manual(self, path: str, name: str = "", alias: str = "") -> bool:
        """手工添加本地绿色软件，默认未授权，并在出错时脱敏提示。
        如果未指定名称，则默认采用目标文件的主文件名；
        支持传入 (path) 或 (path, name, alias) 或 (name, path, alias)。
        """
        effective_path = path
        effective_name = name
        effective_alias = alias

        # 智能识别参数顺序兼容性
        if path and not os.path.exists(path) and not path.lower().endswith((".exe", ".lnk")) and name and (os.path.exists(name) or name.lower().endswith((".exe", ".lnk"))):
            effective_path = name
            effective_name = path

        effective_path = (effective_path or "").strip().strip('"').strip("'")
        if not effective_path:
            self.user_error.emit("没有添加成功，请选择应用程序或安全快捷方式。")
            return False

        if not effective_name:
            effective_name = Path(effective_path).stem

        if not effective_alias:
            effective_alias = effective_name

        try:
            self.catalog_manager.add_manual_app(
                name=effective_name,
                exe_path=effective_path,
                alias=effective_alias,
                authorized=False,
            )
        except (ValueError, FileNotFoundError, PermissionError):
            self.user_error.emit("没有添加成功，请选择应用程序或安全快捷方式。")
            return False
        self.reload()
        return True

    # 别名兼容
    add_manual_app = add_manual

    def repair_manual(self, app_id: str, path: str) -> bool:
        """修复失效应用的主程序路径（支持手动添加应用与自动扫描条目主程序替换）。"""
        clean_path = (path or "").strip().strip('"').strip("'")
        if not clean_path:
            self.user_error.emit("没有修复成功，请重新选择这个应用。")
            return False

        try:
            app = None
            if hasattr(self.catalog_manager, "apps") and isinstance(self.catalog_manager.apps, dict):
                app = self.catalog_manager.apps.get(app_id)
            if app is None and hasattr(self.catalog_manager, "get_all_apps"):
                for a in self.catalog_manager.get_all_apps():
                    if getattr(a, "app_id", None) == app_id:
                        app = a
                        break

            is_manual = (
                getattr(app, "is_user_managed", False)
                or getattr(app, "source", "").lower() == "manual"
            )
            if app is not None and not is_manual and hasattr(self.catalog_manager, "replace_invalid_app_target"):
                self.catalog_manager.replace_invalid_app_target(app_id, clean_path)
            else:
                self.catalog_manager.repair_manual_app(app_id, clean_path)
        except (KeyError, ValueError, FileNotFoundError, PermissionError):
            self.user_error.emit("没有修复成功，请重新选择这个应用。")
            return False
        self.reload()
        return True

    # 别名兼容
    repair_manual_app = repair_manual

    def refresh_scan(self) -> None:
        """在用户已同意扫描的前提下，在后台工作线程更新系统应用列表。"""
        config = self.config_manager.load_config()
        if not config.scan_agreed:
            self.user_error.emit("需要先允许读取 Windows 应用列表。")
            return
        with self._op_lock:
            if self._is_busy:
                return
            self._is_busy = True
            self._scan_request_id += 1
            request_id = self._scan_request_id
        self.busy_changed.emit(True)

        def work() -> None:
            try:
                result = self.catalog_manager.refresh_scan(user_agreed=True)
            except Exception as exc:
                result = exc
            self._scan_finished.emit(request_id, result)

        threading.Thread(target=work, name="CatalogRefresh", daemon=True).start()

    @Slot(int, object)
    def _accept_scan(self, request_id: int, result) -> None:
        """接收后台扫描完成事件，过滤过期请求并刷新界面。"""
        with self._op_lock:
            if request_id != self._scan_request_id:
                return
            self._is_busy = False
        self.busy_changed.emit(False)
        if result is None or isinstance(result, Exception):
            self.user_error.emit("应用列表暂时无法更新，请稍后重试。")
            return
        self.reload()
