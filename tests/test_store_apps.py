from unittest.mock import Mock

import pytest

from src.catalog.store_apps import (
    StoreAppLauncher,
    StoreAppProvider,
    StoreAppRecord,
    StoreActivationError,
    default_store_aliases,
    is_valid_aumid,
)


class FakeItem:
    def __init__(self, name, path):
        self.Name = name
        self.Path = path


def test_apps_folder_keeps_only_valid_aumids():
    items = [
        FakeItem("ChatGPT", "OpenAI.Codex_2p2nqsd0c76g0!App"),
        FakeItem("Desktop Tool", r"C:\\Tools\\tool.exe"),
        FakeItem("Broken", "not an aumid"),
    ]
    provider = StoreAppProvider(items_loader=lambda: items)
    assert provider.enumerate_apps() == [
        StoreAppRecord("ChatGPT", "OpenAI.Codex_2p2nqsd0c76g0!App")
    ]


def test_codex_store_aliases_are_explicit_and_deduplicated():
    aliases = default_store_aliases(
        "ChatGPT", "OpenAI.Codex_2p2nqsd0c76g0!App"
    )
    assert aliases == ["ChatGPT", "Codex", "OpenAI Codex", "codeex"]


@pytest.mark.parametrize(
    "value",
    ["", "C:/Windows/notepad.exe", "shell:AppsFolder", "pkg!app!extra"],
)
def test_invalid_aumid_is_rejected(value):
    assert is_valid_aumid(value) is False


def test_launcher_passes_only_aumid_and_empty_arguments_to_native_activation():
    native = Mock(return_value=1234)
    launcher = StoreAppLauncher(native_activate=native)
    pid = launcher.activate("OpenAI.Codex_2p2nqsd0c76g0!App")
    assert pid == 1234
    native.assert_called_once_with("OpenAI.Codex_2p2nqsd0c76g0!App")


def test_launcher_rejects_arbitrary_non_aumid_before_native_call():
    native = Mock()
    launcher = StoreAppLauncher(native_activate=native)
    with pytest.raises(StoreActivationError):
        launcher.activate(r"C:\\Windows\\System32\\cmd.exe")
    native.assert_not_called()
