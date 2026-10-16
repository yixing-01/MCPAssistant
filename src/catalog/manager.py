"""软件目录管理器
负责软件条目的索引、增删改查、启用/禁用状态切换、重新验证以及安全启动控制。
严格执行两级授权隔离：扫描发现默认未授权，启动必须通过白名单条目二次核验与快捷方式深度安全校验。
"""

import json
import os
import sys
import subprocess
import time
import uuid
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from src.catalog.models import AppEntry, LaunchType, make_stable_identity
from src.catalog.name_quality import (
    calculate_name_quality,
    extract_exe_stem,
    promote_alias_order,
    should_upgrade_display_name,
)
from src.catalog.scanner import SoftwareScanner, is_main_program_candidate
from src.catalog.shortcut_resolver import ShortcutResolver
from src.catalog.store_apps import StoreAppProvider, StoreAppLauncher, is_valid_aumid
from src.catalog.dangerous_targets import is_dangerous_system_program
from src.catalog.window_control import WindowController, WindowCloseResult, CloseStatus
from src.core.config import AppPaths
from src.core.logger import app_logger, sanitize_text

# 高可信主程序来源：仅允许 AppsFolder、AppPaths、开始菜单、桌面、内置工具与手动添加的主程序参与批量授权；坚决排除 Path 与 Registry 启动项
TRUSTED_MAIN_APP_SOURCES = {"AppsFolder", "AppPaths", "StartMenu", "Desktop", "BuiltinTools", "Manual"}




class LookupStatus(str, Enum):
    """软件检索状态"""
    FOUND = "FOUND"
    EXACT = "EXACT"
    UNIQUE_FUZZY = "UNIQUE_FUZZY"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"


@dataclass
class AppLookupResult:
    """软件检索结果结构"""
    status: LookupStatus
    app: Optional[AppEntry] = None
    candidates: List[AppEntry] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.status == LookupStatus.FOUND and self.app is not None

    def __getattr__(self, name):
        if self.app is not None and hasattr(self.app, name):
            return getattr(self.app, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


@dataclass
class ScanResult:
    """软件扫描结果结构化元数据"""
    discovered_ids: List[str] = field(default_factory=list)
    new_ids: List[str] = field(default_factory=list)
    updated_ids: List[str] = field(default_factory=list)
    removed_ids: List[str] = field(default_factory=list)
    scan_id: str = ""
    all_apps: List[AppEntry] = field(default_factory=list)

    def __iter__(self):
        return iter(self.all_apps)

    def __len__(self):
        return len(self.all_apps)


def _normalize_spoken_name(value: str) -> str:
    """去除空格、减号与下划线并转小写的语音规范化名称"""
    return "".join(
        character for character in value.casefold().strip()
        if character not in {" ", "-", "_"}
    )


class CatalogManager:
    """软件目录管理器"""

    def __init__(
        self,
        catalog_file_path: Optional[str] = None,
        store_provider: Optional[StoreAppProvider] = None,
        store_launcher: Optional[StoreAppLauncher] = None,
        window_controller: Optional[WindowController] = None,
    ):
        self.catalog_path = catalog_file_path or AppPaths.get_catalog_file_path()
        self.store_provider = store_provider or StoreAppProvider()
        self.store_launcher = store_launcher or StoreAppLauncher()
        self.window_controller = window_controller or WindowController()
        self.apps: Dict[str, AppEntry] = {}
        self.load_catalog()


    def _validate_launchable(self, app: AppEntry) -> tuple[bool, str]:
        """统一类型感知校验规则：用于单项授权、批量授权、重新验证与启动前终验"""
        if app.launch_type == LaunchType.STORE_AUMID:
            if not is_valid_aumid(app.launch_target):
                return False, "Store 应用标识无效"
            if not self.store_provider.is_installed(app.launch_target):
                return False, "应用未安装或需要重新扫描"
            return True, ""

        path = app.launch_target or app.exe_path
        if not path or not os.path.exists(path) or os.path.isdir(path):
            return False, "应用文件不存在或需要重新选择"

        # 检查启动路径本身是否为高危程序
        is_dang_path, dang_reason = is_dangerous_system_program(path)
        if is_dang_path:
            return False, dang_reason or "高危系统程序禁止授权与启动"

        if app.launch_type == LaunchType.SHORTCUT or path.lower().endswith(".lnk"):
            result = ShortcutResolver.validate(path)
            if not result.is_allowed:
                return False, result.block_reason
            resolved = result.info.target_path if result.info else ""
            current_identity = make_stable_identity(LaunchType.SHORTCUT, path, resolved)
            if app.stable_identity and current_identity != app.stable_identity:
                return False, "快捷方式目标已经变化，需要重新授权"
            if not is_main_program_candidate(resolved, app.display_name):
                return False, "维护、卸载或安装程序禁止授权与启动"
            is_dang_res, dang_res_reason = is_dangerous_system_program(resolved)
            if is_dang_res:
                return False, dang_res_reason or "高危系统程序禁止授权与启动"
            app.resolved_target = resolved
            app.shortcut_arguments = result.info.arguments if result.info else ""
            return True, ""

        if app.launch_type not in (LaunchType.WIN32, LaunchType.PORTABLE):
            return False, "不支持的应用启动类型"
        if not is_main_program_candidate(path, app.display_name):
            return False, "维护、卸载或安装程序禁止授权与启动"
        if app.stable_identity and make_stable_identity(app.launch_type, path) != app.stable_identity:
            return False, "应用文件身份已经变化，需要重新授权"
        return True, ""

    def _apply_validation_result(self, app: AppEntry, is_valid: bool, reason: str) -> None:
        """应用校验结果并脱敏更新状态"""
        if is_valid:
            app.is_valid = True
            app.authorization_block_reason = ""
            app.validation_status = "valid"
        else:
            app.is_valid = False
            app.is_enabled = False
            app.authorization_block_reason = reason
            app.validation_status = "dangerous" if ("高危" in reason or "受限" in reason) else "invalid"

    @staticmethod
    def _launch_identity_key(app: AppEntry) -> str:
        """返回用于等价入口去重的真实启动目标；不安全或无效条目不参与合并。"""
        if not app.is_valid or app.is_restricted or app.authorization_block_reason:
            return ""

        is_shortcut = app.exe_path.lower().endswith(".lnk")
        target = app.resolved_target if is_shortcut else app.exe_path
        if not target:
            return ""

        try:
            return os.path.realpath(os.path.abspath(target)).lower()
        except Exception:
            return ""

    def _deduplicate_equivalent_launch_targets(self) -> bool:
        """合并旧缓存中指向同一真实 EXE 的重复入口，并保留明确授权与自定义别名。"""
        deduplicated: Dict[str, AppEntry] = {}
        by_identity: Dict[str, AppEntry] = {}
        changed = False

        for app in self.apps.values():
            identity = self._launch_identity_key(app)
            existing = by_identity.get(identity) if identity else None
            if existing is None:
                deduplicated[app.app_id] = app
                if identity:
                    by_identity[identity] = app
                continue

            changed = True
            if app.is_enabled and not existing.is_enabled:
                del deduplicated[existing.app_id]
                winner, duplicate = app, existing
                deduplicated[winner.app_id] = winner
                by_identity[identity] = winner
            else:
                winner, duplicate = existing, app

            winner.is_enabled = winner.is_enabled or duplicate.is_enabled
            # 比较 winner 与 duplicate 的名称质量，必要时升级为更高质量的显示名称
            effective_target = winner.resolved_target or winner.exe_path or winner.launch_target
            exe_stem = extract_exe_stem(effective_target)
            win_src = winner.display_name_source or winner.source
            dup_src = duplicate.display_name_source or duplicate.source
            if should_upgrade_display_name(
                current_name=winner.display_name,
                current_source=win_src,
                new_name=duplicate.display_name,
                new_source=dup_src,
                exe_stem=exe_stem,
                is_user_managed=winner.is_user_managed,
            ):
                winner.update_display_name_and_aliases(duplicate.display_name, dup_src)
            else:
                winner.merge_aliases(duplicate.aliases)

            if (
                duplicate.alias
                and duplicate.alias != duplicate.display_name
                and (not winner.alias or winner.alias == winner.display_name)
            ):
                winner.alias = duplicate.alias
            if not winner.publisher and duplicate.publisher:
                winner.publisher = duplicate.publisher
            if not winner.version and duplicate.version:
                winner.version = duplicate.version

        if changed:
            self.apps = deduplicated
        return changed

    def _deduplicate_lookup_candidates(self, candidates: List[AppEntry]) -> List[AppEntry]:
        """查询时把同一稳定身份折叠为一个候选，避免产生虚假歧义。"""
        unique: List[AppEntry] = []
        seen_identities = set()
        for app in candidates:
            identity = app.stable_identity or self._launch_identity_key(app) or f"app-id:{app.app_id}"
            if identity in seen_identities:
                continue
            seen_identities.add(identity)
            unique.append(app)
        return unique

    def load_catalog(self):
        """从磁盘加载软件目录缓存并自动执行安全迁移核验"""
        if not os.path.exists(self.catalog_path):
            self.apps = {}
            return

        try:
            with open(self.catalog_path, "r", encoding="utf-8") as f:
                raw_list = json.load(f)
            self.apps = {item["app_id"]: AppEntry(**item) for item in raw_list}
            self._migrate_and_revalidate_loaded_shortcuts()
            app_logger.info(f"已从缓存加载 {len(self.apps)} 个软件条目并完成快捷方式迁移核验")
        except Exception as e:
            app_logger.warning(f"读取软件目录缓存失败: {e}")
            self.apps = {}

    def _migrate_and_revalidate_loaded_shortcuts(self):
        """
        在加载目录缓存后，对所有条目执行安全迁移与重验：
        - 自动检测并标记不安全、失效、高危或维护/卸载/安装程序，强制撤销授权；
        - 更新安全快捷方式的目标物理路径与参数元数据；
        - 仅在有实际状态变更时写回磁盘缓存文件。
        """
        has_changes = False
        for app in self.apps.values():
            # 兼容迁移旧条目的 alias 到 aliases 事实来源
            if not app.aliases and getattr(app, "alias", None):
                app.aliases = [app.alias.strip()]
                has_changes = True
            elif getattr(app, "alias", None) and app.alias.strip() and app.alias.strip() not in app.aliases:
                app.aliases.append(app.alias.strip())
                has_changes = True

            target_check = app.resolved_target if (app.exe_path and app.exe_path.lower().endswith(".lnk")) else app.exe_path
            # 若条目指向高危系统程序，强制撤销授权并标记拦截原因
            is_dang_t, dang_t_reason = is_dangerous_system_program(target_check or "")
            if not is_dang_t and app.exe_path:
                is_dang_t, dang_t_reason = is_dangerous_system_program(app.exe_path)
            if is_dang_t:
                if app.is_enabled:
                    app.is_enabled = False
                    has_changes = True
                if app.is_valid:
                    app.is_valid = False
                    has_changes = True
                if app.authorization_block_reason != dang_t_reason:
                    app.authorization_block_reason = dang_t_reason
                    app.validation_status = "dangerous"
                    has_changes = True
            elif target_check and not is_main_program_candidate(target_check, app.display_name):
                if app.is_enabled:
                    app.is_enabled = False
                    has_changes = True
                if app.authorization_block_reason != "维护、卸载或安装程序禁止授权与启动":
                    app.authorization_block_reason = "维护、卸载或安装程序禁止授权与启动"
                    app.validation_status = "invalid"
                    has_changes = True

            if app.exe_path and app.exe_path.lower().endswith(".lnk"):
                old_enabled = app.is_enabled
                old_valid = app.is_valid
                old_reason = app.authorization_block_reason
                old_target = app.resolved_target
                old_args = app.shortcut_arguments
                old_status = app.validation_status

                val_res = ShortcutResolver.validate(app.exe_path)
                if val_res.is_allowed:
                    resolved = val_res.info.target_path if val_res.info else ""
                    if resolved and not is_main_program_candidate(resolved, app.display_name):
                        app.is_enabled = False
                        app.is_valid = False
                        app.authorization_block_reason = "维护、卸载或安装程序禁止授权与启动"
                        app.validation_status = "invalid"
                    else:
                        app.resolved_target = resolved
                        app.shortcut_arguments = val_res.info.arguments if val_res.info else ""
                        app.authorization_block_reason = ""
                        app.validation_status = "valid"
                else:
                    app.is_enabled = False
                    app.is_valid = False
                    app.authorization_block_reason = val_res.block_reason
                    app.validation_status = "dangerous" if ("高危" in val_res.block_reason or "受限制" in val_res.block_reason) else "invalid"

                if not app.is_valid and old_enabled and not (app.is_restricted or app.validation_status == "dangerous"):
                    app.requires_user_attention = True

                if (
                    app.is_enabled != old_enabled
                    or app.is_valid != old_valid
                    or app.authorization_block_reason != old_reason
                    or app.resolved_target != old_target
                    or app.shortcut_arguments != old_args
                    or app.validation_status != old_status
                ):
                    has_changes = True

        if self._deduplicate_equivalent_launch_targets():
            has_changes = True

        if self._migrate_display_names():
            has_changes = True

        if has_changes:
            self.save_catalog()

    def _migrate_display_names(self) -> bool:
        """检查并保守迁移旧缓存中的低质量内部名称：
        如果条目未被用户手动管理，且当前 display_name 等于 exe stem 或属于低质量名称，
        而 aliases 列表中存在更高质量的友好名称，则将该友好名称提升为 display_name，
        并将其置于 aliases 首位，保留原有名称在别名中。
        """
        changed = False
        for app in self.apps.values():
            if app.is_user_managed:
                continue
            effective_target = app.resolved_target or app.exe_path or app.launch_target
            exe_stem = extract_exe_stem(effective_target)
            cur_source = app.display_name_source or app.source

            # 遍历 aliases 寻找更高质量的友好候选名称
            best_alias = None
            for candidate in app.aliases:
                clean_cand = str(candidate or "").strip()
                if not clean_cand or clean_cand.casefold() == app.display_name.casefold():
                    continue
                if should_upgrade_display_name(
                    current_name=app.display_name,
                    current_source=cur_source,
                    new_name=clean_cand,
                    new_source="StartMenu",
                    exe_stem=exe_stem,
                    is_user_managed=app.is_user_managed,
                ):
                    if best_alias is None:
                        best_alias = clean_cand
                    else:
                        cand_score = calculate_name_quality(clean_cand, "StartMenu", exe_stem)
                        best_score = calculate_name_quality(best_alias, "StartMenu", exe_stem)
                        if cand_score > best_score:
                            best_alias = clean_cand

            if best_alias:
                app.update_display_name_and_aliases(best_alias, "StartMenu")
                changed = True
        return changed


    def save_catalog(self) -> None:
        """原子保存软件目录至磁盘缓存文件"""
        directory = os.path.dirname(self.catalog_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temp_path = f"{self.catalog_path}.tmp.{os.getpid()}"
        data = [app.model_dump() for app in self.apps.values()]
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.catalog_path)
        except Exception as e:
            app_logger.error(f"保存软件目录缓存失败: {e}")
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            raise

    def refresh_scan(self, user_agreed: bool) -> ScanResult:
        """重新扫描本机已安装软件（原子替换自动集合并删除失效自动条目）"""
        if not user_agreed:
            raise PermissionError("尚未获得用户授权扫描本机软件")

        scan_id = uuid.uuid4().hex
        old_apps = dict(self.apps)
        discovered = SoftwareScanner.scan_all(user_agreed=True)
        next_apps = {
            app_id: app
            for app_id, app in old_apps.items()
            if app.is_user_managed or app.source == "Manual"
        }
        old_by_identity = {
            app.stable_identity: app
            for app in old_apps.values()
            if app.stable_identity
        }
        new_ids = []
        updated_ids = []
        discovered_ids = []

        for item in discovered:
            item.last_seen_scan_id = scan_id
            previous = old_by_identity.get(item.stable_identity)
            if previous is not None:
                if previous.is_user_managed:
                    previous.merge_aliases(item.aliases)
                    previous.last_seen_scan_id = scan_id
                    next_apps[previous.app_id] = previous
                    discovered_ids.append(previous.app_id)
                    updated_ids.append(previous.app_id)
                    continue

                # 比较新旧名称质量，防止低质量名称反向覆盖高质量友好名称
                effective_target = item.resolved_target or item.exe_path or item.launch_target
                exe_stem = extract_exe_stem(effective_target)
                item_src = item.display_name_source or item.source
                prev_src = previous.display_name_source or previous.source

                final_name = item.display_name
                final_source = item_src
                # 如果旧名称评分高于新扫描名称，保留旧名称
                if should_upgrade_display_name(
                    current_name=item.display_name,
                    current_source=item_src,
                    new_name=previous.display_name,
                    new_source=prev_src,
                    exe_stem=exe_stem,
                    is_user_managed=False,
                ):
                    final_name = previous.display_name
                    final_source = prev_src

                item.app_id = previous.app_id
                item.is_enabled = previous.is_enabled
                # 保留合法的 attention 状态，但危险/受限状态优先级最高
                if item.is_restricted or item.validation_status == "dangerous":
                    item.requires_user_attention = False
                elif not item.is_valid and getattr(previous, "requires_user_attention", False):
                    item.requires_user_attention = True
                else:
                    item.requires_user_attention = False
                item.display_name = final_name
                item.display_name_source = final_source
                # 重新合并别名，并确保最友好的名称位于首位
                all_aliases = promote_alias_order(item.aliases + previous.aliases, final_name)
                item.replace_aliases(all_aliases)
                updated_ids.append(item.app_id)
            else:
                item.is_enabled = False
                new_ids.append(item.app_id)
            next_apps[item.app_id] = item
            discovered_ids.append(item.app_id)

        removed_ids = [app_id for app_id in old_apps if app_id not in next_apps]
        self.apps = next_apps
        try:
            self.save_catalog()
        except Exception:
            self.apps = old_apps
            raise

        return ScanResult(
            discovered_ids=discovered_ids,
            new_ids=new_ids,
            updated_ids=updated_ids,
            removed_ids=removed_ids,
            scan_id=scan_id,
            all_apps=list(self.apps.values()),
        )

    def get_all_apps(self) -> List[AppEntry]:
        """获取所有软件条目列表"""
        return list(self.apps.values())

    def search_apps(self, query: str) -> List[AppEntry]:
        """搜索软件（匹配名称、别名或启动目标，用于本地 GUI）"""
        q = query.strip().casefold()
        if not q:
            return self.get_all_apps()

        results = []
        for app in self.apps.values():
            if (
                q in app.display_name.casefold()
                or any(q in a.casefold() for a in app.aliases)
                or q in app.launch_target.casefold()
                or q in app.exe_path.casefold()
            ):
                results.append(app)
        return results

    def set_app_enabled(self, app_id: str, enabled: bool) -> bool:
        """启用或禁用单个软件条目的启动授权"""
        if app_id not in self.apps:
            return False
        app = self.apps[app_id]
        if enabled:
            is_valid, reason = self._validate_launchable(app)
            self._apply_validation_result(app, is_valid, reason)
            if not is_valid:
                if not (app.is_restricted or app.validation_status == "dangerous"):
                    app.requires_user_attention = True
                self.save_catalog()
                app_logger.warning(f"单项授权被拒绝: {app.display_name} [{app_id}] 原因: {reason}")
                return False
            app.is_enabled = True
            app.requires_user_attention = False
        else:
            app.is_enabled = False

        self.save_catalog()
        app_logger.info(f"软件授权状态已更新: {app.display_name} [{app_id}] 授权={app.is_enabled}")
        return True

    def batch_set_enabled(self, authorized: bool, app_ids: Optional[List[str]] = None) -> int:
        """
        批量授权或批量取消授权软件。
        支持限定 app_ids 范围（例如向导仅对本次新发现条目批量授权，不影响用户之前禁用的软件）。
        若 app_ids 为 None，则对全部条目执行。
        """
        count = 0
        target_apps = (
            [self.apps[aid] for aid in app_ids if aid in self.apps]
            if app_ids is not None
            else list(self.apps.values())
        )

        snapshot = {aid: app.model_copy(deep=True) for aid, app in self.apps.items()}
        try:
            if authorized:
                for app in target_apps:
                    # 严格限制来源：仅高可信主程序来源允许批量授权，排除非白名单来源与受限项
                    if app.source not in TRUSTED_MAIN_APP_SOURCES or app.is_restricted:
                        continue

                    is_valid, reason = self._validate_launchable(app)
                    self._apply_validation_result(app, is_valid, reason)
                    if not is_valid:
                        continue

                    if not app.is_enabled:
                        app.is_enabled = True
                        count += 1
            else:
                for app in target_apps:
                    if app.is_enabled:
                        app.is_enabled = False
                        count += 1

            self.save_catalog()
            app_logger.info(f"批量授权操作完成 (授权={authorized}, 范围={'指定项' if app_ids is not None else '全部'})，共影响 {count} 个软件条目")
            return count
        except Exception:
            self.apps = snapshot
            raise

    def add_manual_app(
        self,
        name: str,
        exe_path: str,
        alias: str = "",
        authorized: bool = False
    ) -> AppEntry:
        """用户手工添加便携软件（仅允许 .exe 与 .lnk，明确区分添加与授权）"""
        clean_path = exe_path.strip().strip('"').strip("'")
        if not clean_path:
            raise ValueError("可执行文件路径不能为空")

        if not os.path.exists(clean_path):
            raise FileNotFoundError(f"指定的文件不存在: {clean_path}")

        if os.path.isdir(clean_path):
            raise ValueError("添加目标必须为具体程序文件，不能是文件夹目录")

        ext = os.path.splitext(clean_path)[1].lower()
        if ext not in [".exe", ".lnk"]:
            raise ValueError(f"安全拒绝：仅支持添加 .exe 或 .lnk 可执行文件与快捷方式，不支持脚本或网页文件 ({ext})")

        # 高危系统程序安全拦截
        is_dang, dang_reason = is_dangerous_system_program(clean_path)
        if is_dang:
            raise PermissionError(f"安全拒绝：{dang_reason}")

        norm_path = os.path.realpath(os.path.abspath(clean_path)).lower()

        # 快捷方式安全校验
        resolved_target = ""
        shortcut_args = ""
        block_reason = ""
        val_status = "valid"
        if ext == ".lnk":
            val_res = ShortcutResolver.validate(clean_path)
            if not val_res.is_allowed:
                raise PermissionError(f"安全拒绝：该快捷方式未通过安全校验 ({val_res.block_reason})，禁止添加")
            else:
                resolved_target = val_res.info.target_path if val_res.info else ""
                shortcut_args = val_res.info.arguments if val_res.info else ""
                is_dang_res, dang_res_reason = is_dangerous_system_program(resolved_target)
                if is_dang_res:
                    raise PermissionError(f"安全拒绝：{dang_res_reason}")

        # 检查是否已存在相同路径条目
        for existing in self.apps.values():
            if os.path.realpath(os.path.abspath(existing.exe_path)).lower() == norm_path:
                existing.display_name = name.strip() or existing.display_name
                clean_alias = alias.strip()
                if clean_alias:
                    existing.alias = clean_alias
                    # 以 aliases 列表为唯一运行时事实来源，规范化去重合并
                    current_aliases = [a.strip() for a in existing.aliases if a and a.strip()]
                    seen_cf = {a.casefold() for a in current_aliases}
                    if clean_alias.casefold() not in seen_cf:
                        current_aliases.append(clean_alias)
                    existing.replace_aliases(current_aliases)
                existing.is_user_managed = True
                existing.is_enabled = authorized and (not block_reason)
                existing.is_valid = True if not block_reason else False
                existing.resolved_target = resolved_target
                existing.shortcut_arguments = shortcut_args
                existing.authorization_block_reason = block_reason
                existing.validation_status = val_status
                self.save_catalog()
                app_logger.info(f"手工更新已存在软件: {name} (授权={existing.is_enabled})")
                return existing

        app_id = SoftwareScanner._generate_app_id(name.strip(), norm_path)
        kind = LaunchType.SHORTCUT if clean_path.lower().endswith(".lnk") else LaunchType.PORTABLE
        identity = make_stable_identity(kind, clean_path, resolved_target)
        entry = AppEntry(
            app_id=app_id,
            display_name=name.strip(),
            alias=alias.strip() or name.strip(),
            aliases=[alias.strip()] if alias.strip() else ([name.strip()] if name.strip() else []),
            exe_path=clean_path,
            launch_type=kind,
            launch_target=clean_path,
            stable_identity=identity,
            source="Manual",
            is_user_managed=True,
            is_enabled=authorized and (not block_reason),
            is_valid=True if not block_reason else False,
            resolved_target=resolved_target,
            shortcut_arguments=shortcut_args,
            authorization_block_reason=block_reason,
            validation_status=val_status
        )
        self.apps[app_id] = entry
        self.save_catalog()
        app_logger.info(f"成功手工添加软件: {name} [{entry.app_id}], 授权={entry.is_enabled}")
        return entry

    def repair_manual_app(self, app_id: str, new_path: str) -> AppEntry:
        """替换失效手动应用的目标；目标身份变化后必须重新授权。"""
        current = self.apps.get(app_id)
        if current is None:
            raise KeyError("指定应用不存在")
        if not current.is_user_managed:
            raise PermissionError("仅允许修复手动添加的应用")

        clean_path = new_path.strip().strip('"').strip("'")
        if not clean_path or not os.path.exists(clean_path) or os.path.isdir(clean_path):
            raise FileNotFoundError("重新选择的应用文件不存在")
        extension = os.path.splitext(clean_path)[1].lower()
        if extension not in {".exe", ".lnk"}:
            raise ValueError("只支持选择应用程序或安全快捷方式")

        resolved_target = ""
        shortcut_arguments = ""
        kind = LaunchType.PORTABLE
        if extension == ".lnk":
            validation = ShortcutResolver.validate(clean_path)
            if not validation.is_allowed:
                raise PermissionError("重新选择的快捷方式未通过安全检查")
            kind = LaunchType.SHORTCUT
            resolved_target = validation.info.target_path if validation.info else ""
            shortcut_arguments = validation.info.arguments if validation.info else ""
        # 拦截高危程序
        is_dang_repair, dang_repair_reason = is_dangerous_system_program(clean_path)
        if not is_dang_repair and resolved_target:
            is_dang_repair, dang_repair_reason = is_dangerous_system_program(resolved_target)
        if is_dang_repair:
            raise PermissionError(f"安全拒绝：{dang_repair_reason}")

        identity = make_stable_identity(kind, clean_path, resolved_target)
        if any(
            other_id != app_id and other.stable_identity == identity
            for other_id, other in self.apps.items()
        ):
            raise ValueError("这个应用已经在列表中")

        snapshot = dict(self.apps)
        repaired = current.model_copy(deep=True)
        repaired.launch_type = kind
        repaired.launch_target = clean_path
        repaired.exe_path = "" if kind == LaunchType.STORE_AUMID else clean_path
        repaired.resolved_target = resolved_target
        repaired.shortcut_arguments = shortcut_arguments
        repaired.stable_identity = identity
        repaired.is_valid = True
        repaired.is_enabled = False
        repaired.requires_user_attention = False
        repaired.is_restricted = False
        repaired.authorization_block_reason = ""
        repaired.validation_status = "valid"
        repaired.last_verified = time.time()
        self.apps[app_id] = repaired
        try:
            self.save_catalog()
        except Exception:
            self.apps = snapshot
            raise
        return repaired

    def replace_invalid_app_target(self, app_id: str, new_path: str) -> AppEntry:
        """为扫描发现但校验失效的可修复条目重新指定可执行程序；修复后转为手动管理安全条目并保持未授权。"""
        current = self.apps.get(app_id)
        if current is None:
            raise KeyError("指定应用不存在")
        if current.is_restricted or current.validation_status == "dangerous":
            raise PermissionError("安全拒绝：高危系统工具或受限应用禁止修复")
        if current.is_valid:
            raise ValueError("该应用当前有效，无需修复")

        clean_path = new_path.strip().strip('"').strip("'")
        if not clean_path:
            raise FileNotFoundError("重新选择的应用文件不存在")

        # 无论文件是否存在，优先拦截高危系统程序
        is_dang_repair, dang_repair_reason = is_dangerous_system_program(clean_path)
        if is_dang_repair:
            raise PermissionError(f"安全拒绝：{dang_repair_reason}")

        if not os.path.exists(clean_path) or os.path.isdir(clean_path):
            raise FileNotFoundError("重新选择的应用文件不存在")
        extension = os.path.splitext(clean_path)[1].lower()
        if extension not in {".exe", ".lnk"}:
            raise ValueError("只支持选择应用程序或安全快捷方式")

        # 检查是否为主程序候选
        if not is_main_program_candidate(clean_path, current.display_name):
            raise PermissionError("维护程序、卸载程序或安装程序禁止选择")

        resolved_target = ""
        shortcut_arguments = ""
        kind = LaunchType.PORTABLE
        if extension == ".lnk":
            validation = ShortcutResolver.validate(clean_path)
            if not validation.is_allowed:
                raise PermissionError(f"重新选择的快捷方式未通过安全检查 ({validation.block_reason})")
            kind = LaunchType.SHORTCUT
            resolved_target = validation.info.target_path if validation.info else ""
            shortcut_arguments = validation.info.arguments if validation.info else ""
            if not is_main_program_candidate(resolved_target, current.display_name):
                raise PermissionError("维护程序、卸载程序或安装程序禁止选择")
            if resolved_target:
                is_dang_res, dang_res_reason = is_dangerous_system_program(resolved_target)
                if is_dang_res:
                    raise PermissionError(f"安全拒绝：{dang_res_reason}")

        identity = make_stable_identity(kind, clean_path, resolved_target)
        if any(
            other_id != app_id and other.stable_identity == identity
            for other_id, other in self.apps.items()
        ):
            raise ValueError("这个应用已经在列表中")

        snapshot = dict(self.apps)
        repaired = current.model_copy(deep=True)
        repaired.launch_type = kind
        repaired.launch_target = clean_path
        repaired.exe_path = clean_path
        repaired.resolved_target = resolved_target
        repaired.shortcut_arguments = shortcut_arguments
        repaired.stable_identity = identity
        repaired.is_user_managed = True  # 转换为用户手动管理条目
        repaired.source = "Manual"
        repaired.is_valid = True
        repaired.is_enabled = False  # 必须保持未授权，等待用户再次允许
        repaired.requires_user_attention = False
        repaired.is_restricted = False
        repaired.authorization_block_reason = ""
        repaired.validation_status = "valid"
        repaired.last_verified = time.time()
        self.apps[app_id] = repaired
        try:
            self.save_catalog()
        except Exception:
            self.apps = snapshot
            raise
        app_logger.info(f"成功为失效条目重选主程序: {current.display_name} [{app_id}] -> {clean_path}")
        return repaired

    def revalidate_all(self):
        """重新验证所有软件的物理有效性、安全性与启动条件"""
        changed = False
        for app in self.apps.values():
            old_valid = app.is_valid
            old_enabled = app.is_enabled
            old_reason = app.authorization_block_reason
            is_valid, reason = self._validate_launchable(app)
            self._apply_validation_result(app, is_valid, reason)
            if old_enabled and not is_valid and not (app.is_restricted or app.validation_status == "dangerous"):
                app.requires_user_attention = True
            elif is_valid:
                app.requires_user_attention = False
            if (
                app.is_valid != old_valid
                or app.is_enabled != old_enabled
                or app.authorization_block_reason != old_reason
            ):
                changed = True
        if changed:
            self.save_catalog()

    def find_authorized_app(self, program_name_or_alias: str) -> AppLookupResult:
        """
        根据小智传入的名称或别名查找已授权且有效的软件。
        匹配优先级：
        1. 遍历所有别名的不区分大小写精确匹配
        2. 显示名称不区分大小写精确匹配
        3. 安全规范化名称/别名精确匹配（去除空格、减号、下划线）
        4. 有限度子串匹配（遍历显示名称与全部别名）
        若任一优先级内命中多个不同稳定身份候选，返回歧义状态并包含所有候选，绝不自动选择第一个。
        """
        q = (program_name_or_alias or "").strip()
        if not q:
            return AppLookupResult(status=LookupStatus.NOT_FOUND, app=None, candidates=[])

        target_lower = q.casefold()
        target_norm = _normalize_spoken_name(q)

        # 仅在已授权、有效、非受限软件中匹配
        valid_apps = [
            app for app in self.apps.values()
            if app.is_enabled and app.is_valid and not app.is_restricted
        ]

        # 1. 优先：遍历所有别名进行精确匹配（不区分大小写）
        alias_matches = self._deduplicate_lookup_candidates([
            app for app in valid_apps
            if any(a.casefold() == target_lower for a in app.aliases)
        ])
        if len(alias_matches) == 1:
            return AppLookupResult(status=LookupStatus.FOUND, app=alias_matches[0], candidates=alias_matches)
        elif len(alias_matches) > 1:
            return AppLookupResult(status=LookupStatus.AMBIGUOUS, app=None, candidates=alias_matches)

        # 2. 规范化别名/显示名匹配（去除空格、减号、下划线，识别语音同形歧义）
        if target_norm:
            norm_matches = self._deduplicate_lookup_candidates([
                app for app in valid_apps
                if _normalize_spoken_name(app.display_name) == target_norm
                or any(_normalize_spoken_name(a) == target_norm for a in app.aliases)
            ])
            if len(norm_matches) == 1:
                return AppLookupResult(status=LookupStatus.FOUND, app=norm_matches[0], candidates=norm_matches)
            elif len(norm_matches) > 1:
                return AppLookupResult(status=LookupStatus.AMBIGUOUS, app=None, candidates=norm_matches)

        # 3. 显示名称精确匹配（不区分大小写）
        name_matches = self._deduplicate_lookup_candidates([
            app for app in valid_apps
            if app.display_name.casefold() == target_lower
        ])
        if len(name_matches) == 1:
            return AppLookupResult(status=LookupStatus.FOUND, app=name_matches[0], candidates=name_matches)
        elif len(name_matches) > 1:
            return AppLookupResult(status=LookupStatus.AMBIGUOUS, app=None, candidates=name_matches)

        # 4. 兜底：有限度子串匹配（遍历显示名称与全部别名）
        fuzzy_matches = self._deduplicate_lookup_candidates([
            app for app in valid_apps
            if target_lower in app.display_name.casefold()
            or any(target_lower in a.casefold() for a in app.aliases)
        ])
        if len(fuzzy_matches) == 1:
            return AppLookupResult(status=LookupStatus.FOUND, app=fuzzy_matches[0], candidates=fuzzy_matches)
        elif len(fuzzy_matches) > 1:
            return AppLookupResult(status=LookupStatus.AMBIGUOUS, app=None, candidates=fuzzy_matches)

        return AppLookupResult(status=LookupStatus.NOT_FOUND, app=None, candidates=[])

    def launch_app(self, app_entry: AppEntry) -> str:
        """
        启动经授权的软件。
        必须从正式 self.apps 中重新取得条目进行二次核验，严防伪造 AppEntry。
        启动前通过统一校验规则并按 launch_type 分发启动。
        """
        if not app_entry or not app_entry.app_id:
            raise PermissionError("安全拒绝：无效的软件启动请求")

        formal_entry = self.apps.get(app_entry.app_id)
        if not formal_entry:
            raise PermissionError("安全拒绝：指定的软件条目未在白名单目录中注册，禁止启动")

        if formal_entry.is_restricted:
            raise PermissionError(f"安全拒绝：软件 [{formal_entry.display_name}] 为受限/暂不支持类型，禁止启动")

        if not formal_entry.is_enabled:
            raise PermissionError(f"安全拒绝：软件 [{formal_entry.display_name}] 尚未被用户授权启动")

        target_check = formal_entry.resolved_target or formal_entry.launch_target or formal_entry.exe_path
        is_dang_l, dang_l_reason = is_dangerous_system_program(target_check or "")
        if not is_dang_l and formal_entry.exe_path:
            is_dang_l, dang_l_reason = is_dangerous_system_program(formal_entry.exe_path)
        if is_dang_l:
            raise PermissionError(f"安全拒绝：{dang_l_reason or '高危系统程序禁止启动'}")

        is_valid, reason = self._validate_launchable(formal_entry)
        self._apply_validation_result(formal_entry, is_valid, reason)
        if not is_valid:
            self.save_catalog()
            if "不存在" in reason or "未安装" in reason:
                raise FileNotFoundError(f"启动失败：{reason}")
            raise PermissionError(f"安全拒绝：{reason}")

        app_logger.info(f"正在启动已授权软件: {formal_entry.display_name} [{formal_entry.app_id}]")

        launch_failed = False
        try:
            if formal_entry.launch_type == LaunchType.STORE_AUMID:
                self.store_launcher.activate(formal_entry.launch_target)
            elif formal_entry.launch_type == LaunchType.SHORTCUT:
                if sys.platform == "win32":
                    os.startfile(formal_entry.launch_target)
                else:
                    subprocess.Popen([formal_entry.launch_target], shell=False)
            elif formal_entry.launch_type in (LaunchType.WIN32, LaunchType.PORTABLE):
                subprocess.Popen([formal_entry.launch_target], shell=False)
            else:
                raise PermissionError("安全拒绝：不支持的应用启动类型")
        except PermissionError:
            raise
        except Exception as e:
            launch_failed = True
            app_logger.error(
                f"系统启动进程失败: [{formal_entry.display_name}] [{formal_entry.app_id}], "
                f"错误类型: {type(e).__name__}"
            )

        if launch_failed:
            # 在 except 块外显式抛出固定分类描述异常，彻底斩断 __cause__ 与 __context__ 异常链，防止底层敏感路径泄漏
            raise RuntimeError("调用系统启动进程失败")

        return f"软件已成功启动: {formal_entry.display_name}"

    def close_app(self, app_entry: AppEntry) -> WindowCloseResult:
        """
        关闭经授权软件的全部可见主窗口。
        必须从正式 self.apps 中重新取得条目进行二次核验，严防伪造 AppEntry。
        关闭前通过统一校验规则并调用 WindowController 执行关闭。
        """
        if not app_entry or not app_entry.app_id:
            raise PermissionError("安全拒绝：无效的软件关闭请求")

        formal_entry = self.apps.get(app_entry.app_id)
        if not formal_entry:
            raise PermissionError("安全拒绝：指定的软件条目未在白名单目录中注册，禁止关闭")

        if formal_entry.is_restricted:
            raise PermissionError(f"安全拒绝：软件 [{formal_entry.display_name}] 为受限/暂不支持类型，禁止关闭")

        if not formal_entry.is_enabled:
            raise PermissionError(f"安全拒绝：软件 [{formal_entry.display_name}] 尚未被用户授权控制，禁止关闭")

        is_valid, reason = self._validate_launchable(formal_entry)
        self._apply_validation_result(formal_entry, is_valid, reason)
        if not is_valid:
            self.save_catalog()
            if "不存在" in reason or "未安装" in reason:
                raise FileNotFoundError(f"关闭失败：{reason}")
            raise PermissionError(f"安全拒绝：{reason}")

        app_logger.info(f"正在请求关闭已授权软件主窗口: {formal_entry.display_name} [{formal_entry.app_id}]")
        return self.window_controller.close_app_windows(formal_entry)

