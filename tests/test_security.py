"""安全与沙箱引擎单元测试"""

import pytest
import tempfile
import os
import socket
from unittest.mock import patch
from src.core.security import (
    SafeCalculator,
    UrlValidator,
    PathSandbox,
    CommandValidator,
)


def test_safe_calculator_valid_math():
    """测试合法数学运算表达式"""
    assert SafeCalculator.evaluate("1 + 2 * 3") == 7
    assert SafeCalculator.evaluate("(10 - 2) / 4") == 2.0
    assert SafeCalculator.evaluate("2 ** 10") == 1024
    assert SafeCalculator.evaluate("abs(-42)") == 42
    assert SafeCalculator.evaluate("round(3.14159, 2)") == 3.14
    assert SafeCalculator.evaluate("min(5, 1, 9)") == 1
    assert SafeCalculator.evaluate("max([10, 20, 5])") == 20
    assert SafeCalculator.evaluate("sum([1, 2, 3, 4])") == 10
    assert SafeCalculator.evaluate("sqrt(16)") == 4.0
    assert SafeCalculator.evaluate("math.sqrt(25)") == 5.0
    assert abs(SafeCalculator.evaluate("pi") - 3.14159265) < 1e-5


def test_safe_calculator_forbidden_injection():
    """测试阻止任意代码注入与恶意调用"""
    # 试图导入系统库
    with pytest.raises(ValueError):
        SafeCalculator.evaluate("__import__('os').system('calc')")

    # 试图执行任意内建函数
    with pytest.raises(ValueError):
        SafeCalculator.evaluate("open('C:\\test.txt')")

    # 尝试访问敏感属性
    with pytest.raises(ValueError):
        SafeCalculator.evaluate("().__class__.__bases__[0]")

    # 除零异常保护
    with pytest.raises(ZeroDivisionError):
        SafeCalculator.evaluate("10 / 0")

    # 拒绝超大指数防止 DoS
    with pytest.raises(ValueError):
        SafeCalculator.evaluate("2 ** 999999")


def test_url_validator():
    """测试 URL 协议白名单限制"""
    # 合法 http/https（打桩公网 IP 防范本地 DNS 异常）
    fake_public_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('180.101.50.242', 443))]
    with patch("socket.getaddrinfo", return_value=fake_public_dns):
        assert UrlValidator.validate_url("https://www.baidu.com") == "https://www.baidu.com"
    
    # 拒绝本地回环与私网地址（Item 9 严格安全要求）
    with pytest.raises(ValueError):
        UrlValidator.validate_url("http://127.0.0.1:8080/test")

    # 危险协议应被彻底拒绝
    with pytest.raises(ValueError):
        UrlValidator.validate_url("file:///C:/Windows/System32/cmd.exe")

    with pytest.raises(ValueError):
        UrlValidator.validate_url("javascript:alert(1)")

    with pytest.raises(ValueError):
        UrlValidator.validate_url("data:text/html;base64,PHNjcmlwdD4=")

    with pytest.raises(ValueError):
        UrlValidator.validate_url("")


def test_path_sandbox():
    """测试受限文件写入沙箱"""
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as temp_dir:
        allowed_dirs = [temp_dir]

        with patch.object(PathSandbox, "is_sensitive_path", return_value=(False, "")):
            # 允许在受信任目录内写入
            valid_path = os.path.join(temp_dir, "test.txt")
            assert PathSandbox.validate_write_path(valid_path, allowed_dirs) == os.path.realpath(valid_path)

            # 拒绝目录穿越到上级目录
            traversal_path = os.path.join(temp_dir, "..", "evil.txt")
            with pytest.raises(ValueError, match="安全拒绝"):
                PathSandbox.validate_write_path(traversal_path, allowed_dirs)

            # 拒绝 UNC 远程网络路径
            with pytest.raises(ValueError, match="安全拒绝"):
                PathSandbox.validate_write_path(r"\\192.168.1.1\share\test.txt", allowed_dirs)

        # 拒绝写入系统目录（真实敏感检查）
        with pytest.raises(ValueError, match="安全拒绝"):
            PathSandbox.validate_write_path(r"C:\Windows\System32\malicious.dll", allowed_dirs)


def test_command_validator():
    """测试命令执行白名单控制"""
    whitelist = {
        "IP配置": "ipconfig",
        "系统信息": "systeminfo"
    }

    # 当总开关关闭时，拒绝一切命令
    with pytest.raises(PermissionError, match="命令执行功能已被用户关闭"):
        CommandValidator.resolve_command("IP配置", whitelist, allow_cmd=False)

    # 当总开关开启时，仅允许白名单
    cmd = CommandValidator.resolve_command("IP配置", whitelist, allow_cmd=True)
    assert cmd == "ipconfig"

    cmd2 = CommandValidator.resolve_command("ipconfig", whitelist, allow_cmd=True)
    assert cmd2 == "ipconfig"

    # 拒绝任意未在白名单中的危险命令
    with pytest.raises(PermissionError, match="不在命令白名单中"):
        CommandValidator.resolve_command("format C: /y", whitelist, allow_cmd=True)

    with pytest.raises(PermissionError, match="不在命令白名单中"):
        CommandValidator.resolve_command("powershell -c evil", whitelist, allow_cmd=True)
