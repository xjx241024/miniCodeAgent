"""输出治理测试：超阈值全文落盘 artifact，模型收到预览 + 精读提示。"""

from __future__ import annotations

from runtime.output_guard import OutputGuard
from tools.base import ToolResult


def _big_text(lines: int = 300, width: int = 40) -> str:
    """造一个多行大文本。"""
    return "\n".join(f"line-{i:03d}-" + "x" * width for i in range(lines))


def test_small_output_unchanged(tmp_path):
    """未超阈值时原样返回，且不产生 artifact 文件。"""
    guard = OutputGuard(workspace_root=tmp_path, data_dir=tmp_path)
    result = ToolResult.success(data={"k": 1}, text="short")
    guarded = guard.guard("bash", result)
    assert guarded is result  # 零副作用：同一个对象
    assert not guard.artifact_dir.exists()


def test_long_output_governed(tmp_path):
    """超过字符阈值：正文落盘，text 被替换为预览 + 路径 + 精读提示。"""
    guard = OutputGuard(workspace_root=tmp_path, data_dir=tmp_path, max_chars=200)
    body = _big_text(20, 20)  # 20 行，足够长
    result = ToolResult.success(data={"returncode": 0}, text=body)
    guarded = guard.guard("bash", result, session_id="s1")
    # 正文写入 artifact 目录
    artifacts = list(guard.artifact_dir.glob("*.txt"))
    assert len(artifacts) == 1
    with artifacts[0].open(encoding="utf-8") as f:
        assert f.read() == body
    # 返回文本包含预览与路径提示
    assert "line-000" in guarded.text
    assert "完整内容已保存到" in guarded.text
    assert str(artifacts[0]) in guarded.text
    assert len(guarded.text) < len(body)
    # 元数据不丢失
    assert guarded.data == {"returncode": 0}


def test_many_lines_triggers_governance(tmp_path):
    """行数超预览上限也触发治理（短行大日志）。"""
    guard = OutputGuard(workspace_root=tmp_path, data_dir=tmp_path, preview_lines=10)
    body = _big_text(50, 10)  # 50 行但每行很短
    result = ToolResult.success(text=body)
    guarded = guard.guard("bash", result)
    assert "已超阈值" in guarded.text
    assert len(list(guard.artifact_dir.glob("*.txt"))) == 1


def test_error_result_without_text_unchanged(tmp_path):
    """error 且 text 为空的结果不触发治理（错误码由 loop 层回填）。"""
    guard = OutputGuard(workspace_root=tmp_path, data_dir=tmp_path)
    result = ToolResult.failure(code="EXIT_NONZERO", message="boom")
    guarded = guard.guard("bash", result)
    assert guarded is result