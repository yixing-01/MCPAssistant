"""MCP助手应用图标加载层与全局生命周期缓存（AppIconProvider）。

职责：
1. 提取 Windows 原生 Win32、Shortcut(.lnk)、Portable 及 Store/MSIX(AUMID) 软件图标；
2. 保持透明通道、高分辨率平滑缩放与宽高比；
3. 应用生命周期级内存缓存与在途请求去重（不重复提取、不卡顿界面）；
4. 批量后台异步提取，QPixmap 与 UI 控件设置严格限制于 GUI 主线程；
5. 原生提取失败时优雅回退至中性通用应用图标，绝不显示蓝色首字母方块。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional
from PIL import Image

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from src.catalog.windows_metadata import _ComScope, load_shell_icon

FALLBACK_ICON_PATH = (
    Path(__file__).resolve().parent / "resources" / "icons" / "app.svg"
)


def _normalize_dpr(dpr: float | None = None) -> float:
    """标准化设备像素比，并提供当前屏幕默认 DPR 探测。"""
    if dpr is not None and dpr > 0:
        return round(float(dpr), 2)
    app = QApplication.instance()
    if app:
        screen = app.primaryScreen()
        if screen:
            return round(float(screen.devicePixelRatio()), 2)
    return 1.0


def pillow_to_qpixmap(img: Image.Image) -> QPixmap:
    """将 Pillow RGBA 图像安全转换为 QPixmap，完整保留 Alpha 透明通道。"""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    raw_bytes = img.tobytes("raw", "RGBA")
    qimg = QImage(
        raw_bytes,
        img.width,
        img.height,
        img.width * 4,
        QImage.Format.Format_RGBA8888,
    ).copy()
    return QPixmap.fromImage(qimg)


class _IconWorkerSignals(QObject):
    """用于从后台工作线程向 GUI 主线程安全回传 PIL 图像的 Qt 信号。"""
    loaded = Signal(str, str, int, float, object)


class _IconLoadWorker(QRunnable):
    """在后台线程池中调用 Windows Shell API 提取图标，避免阻塞 GUI 事件循环。"""

    def __init__(
        self,
        icon_ref: str,
        launch_type: str,
        size: int,
        dpr: float,
        signals: _IconWorkerSignals,
    ):
        super().__init__()
        self.icon_ref = icon_ref
        self.launch_type = launch_type
        self.size = size
        self.dpr = dpr
        self.signals = signals

    def run(self) -> None:
        img: Optional[Image.Image] = None
        phys_size = max(16, int(round(self.size * self.dpr)))
        with _ComScope():
            try:
                img = load_shell_icon(self.icon_ref, self.launch_type, phys_size)
            except Exception:
                img = None

        try:
            self.signals.loaded.emit(
                self.icon_ref, self.launch_type, self.size, self.dpr, img
            )
        except RuntimeError:
            pass


class _DprAwareCache(dict):
    """支持 4 元组键 (ref, type, size, dpr) 并兼容 3 元组键 (ref, type, size) 查询的缓存字典。"""

    def __contains__(self, key):
        if super().__contains__(key):
            return True
        if isinstance(key, tuple) and len(key) == 3:
            return any(k[:3] == key for k in self.keys())
        return False

    def __getitem__(self, key):
        try:
            return super().__getitem__(key)
        except KeyError:
            if isinstance(key, tuple) and len(key) == 3:
                for k, v in self.items():
                    if k[:3] == key:
                        return v
            raise


class _DprAwareSet(set):
    """支持 4 元组并兼容 3 元组查询的失败记录集合。"""

    def __contains__(self, key):
        if super().__contains__(key):
            return True
        if isinstance(key, tuple) and len(key) == 3:
            return any(k[:3] == key for k in self)
        return False


class AppIconProvider(QObject):
    """全局应用图标 Provider，提供内存缓存、在途去重与异步加载调度。"""

    _instance: Optional["AppIconProvider"] = None

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._pixmap_cache: dict[tuple[str, str, int, float], QPixmap] = _DprAwareCache()
        self._failed_cache: set[tuple[str, str, int, float]] = _DprAwareSet()
        self._pending_requests: dict[
            tuple[str, str, int, float], list[Callable[[QPixmap], None]]
        ] = {}
        self._default_pixmaps: dict[tuple[int, float], QPixmap] = {}

        self._signals = _IconWorkerSignals()
        self._signals.loaded.connect(self._on_worker_completed)

    @classmethod
    def instance(cls) -> "AppIconProvider":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_default_fallback_pixmap(
        self, size: int = 36, dpr: float | None = None
    ) -> QPixmap:
        """获取中性通用应用图标，绝不显示字母，支持高 DPI。"""
        norm_dpr = _normalize_dpr(dpr)
        cache_key = (size, norm_dpr)
        if cache_key in self._default_pixmaps:
            return self._default_pixmaps[cache_key]

        phys_size = max(16, int(round(size * norm_dpr)))
        pixmap = QPixmap()
        if FALLBACK_ICON_PATH.exists():
            icon = QIcon(str(FALLBACK_ICON_PATH))
            if not icon.isNull():
                pixmap = icon.pixmap(phys_size, phys_size)

        if pixmap.isNull():
            # 备用：纯代码绘制现代柔和的通用软件方块
            pixmap = QPixmap(phys_size, phys_size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor("#EDF0F7"))
            painter.setPen(QColor("#D0D5DD"))
            painter.drawRoundedRect(2, 2, phys_size - 4, phys_size - 4, int(8 * norm_dpr), int(8 * norm_dpr))
            painter.end()

        pixmap.setDevicePixelRatio(norm_dpr)
        self._default_pixmaps[cache_key] = pixmap
        return pixmap

    def get_cached_pixmap(
        self,
        icon_ref: str,
        launch_type: str = "win32",
        size: int = 36,
        dpr: float | None = None,
    ) -> Optional[QPixmap]:
        """同步检查缓存：若已缓存则直接返回，若已记录失败则返回中性图标，否则返回 None。"""
        norm_dpr = _normalize_dpr(dpr)
        key = (icon_ref, str(launch_type).lower(), size, norm_dpr)
        if key in self._pixmap_cache:
            return self._pixmap_cache[key]
        if key in self._failed_cache:
            return self.get_default_fallback_pixmap(size, norm_dpr)
        return None

    def load_icon_async(
        self,
        icon_ref: str,
        launch_type: str = "win32",
        size: int = 36,
        callback: Optional[Callable[[QPixmap], None]] = None,
        dpr: float | None = None,
    ) -> None:
        """异步请求图标。若已缓存立即回调；若正在提取则追加监听；否则发起后台任务。"""
        norm_dpr = _normalize_dpr(dpr)
        if not icon_ref:
            if callback:
                callback(self.get_default_fallback_pixmap(size, norm_dpr))
            return

        key = (icon_ref, str(launch_type).lower(), size, norm_dpr)

        # 1. 命中成功缓存
        if key in self._pixmap_cache:
            if callback:
                callback(self._pixmap_cache[key])
            return

        # 2. 命中失败缓存
        if key in self._failed_cache:
            if callback:
                callback(self.get_default_fallback_pixmap(size, norm_dpr))
            return

        # 3. 在途请求去重：若已有相同在途任务，仅登记回调，不重复启动后台任务
        if key in self._pending_requests:
            if callback:
                self._pending_requests[key].append(callback)
            return

        # 4. 首次请求：记录在途并提交至后台线程池
        self._pending_requests[key] = [callback] if callback else []
        worker = _IconLoadWorker(icon_ref, str(launch_type), size, norm_dpr, self._signals)
        QThreadPool.globalInstance().start(worker)

    def load_icon_sync(
        self,
        icon_ref: str,
        launch_type: str = "win32",
        size: int = 36,
        dpr: float | None = None,
    ) -> QPixmap:
        """同步提取图标（供测试、快速探测或单次预览使用）。"""
        norm_dpr = _normalize_dpr(dpr)
        key = (icon_ref, str(launch_type).lower(), size, norm_dpr)
        if key in self._pixmap_cache:
            return self._pixmap_cache[key]
        if key in self._failed_cache:
            return self.get_default_fallback_pixmap(size, norm_dpr)

        phys_size = max(16, int(round(size * norm_dpr)))
        try:
            img = load_shell_icon(icon_ref, launch_type, phys_size)
            if img:
                pix = pillow_to_qpixmap(img)
                pix.setDevicePixelRatio(norm_dpr)
                self._pixmap_cache[key] = pix
                return pix
        except Exception:
            pass

        self._failed_cache.add(key)
        return self.get_default_fallback_pixmap(size, norm_dpr)

    def _on_worker_completed(
        self,
        icon_ref: str,
        launch_type: str,
        size: int,
        dpr: float,
        pil_img: Optional[Image.Image],
    ) -> None:
        """后台提取完成后的主线程信号响应函数。"""
        key = (icon_ref, str(launch_type).lower(), size, dpr)

        if pil_img is not None:
            try:
                pixmap = pillow_to_qpixmap(pil_img)
                pixmap.setDevicePixelRatio(dpr)
                self._pixmap_cache[key] = pixmap
            except Exception:
                self._failed_cache.add(key)
                pixmap = self.get_default_fallback_pixmap(size, dpr)
        else:
            self._failed_cache.add(key)
            pixmap = self.get_default_fallback_pixmap(size, dpr)

        callbacks = self._pending_requests.pop(key, [])
        for cb in callbacks:
            try:
                cb(pixmap)
            except Exception:
                pass

    def clear_cache(self) -> None:
        """清空缓存（主要用于单元测试与基准重置）。"""
        self._pixmap_cache.clear()
        self._failed_cache.clear()
        self._pending_requests.clear()
        self._default_pixmaps.clear()

    def get_cache_stats(self) -> dict[str, int]:
        """获取当前缓存指标。"""
        return {
            "cached": len(self._pixmap_cache),
            "failed": len(self._failed_cache),
            "pending": len(self._pending_requests),
        }
