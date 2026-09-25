# -*- coding: utf-8 -*-
"""截图 runner 单测：重点覆盖「删友链自动清理图床孤儿图」。"""

import json

import pytest

from friend_circle_lite.screenshots import runner


def _write_link(path, hosts):
    data = {"link_data": [{"name": h, "link": f"https://{h}/"} for h in hosts]}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_cleanup_removed_deletes_orphan_screenshots(tmp_path, monkeypatch):
    """上一轮有、本轮消失的 host，其图床文件应被 delete_from_imagebed 删除。"""
    prev = tmp_path / "prev_link.json"
    _write_link(prev, ["keep.example.com", "old.example.com", "gone.example.com"])

    cur = tmp_path / "cur_link.json"
    _write_link(cur, ["keep.example.com"])  # old / gone 本轮已移除

    calls = []
    monkeypatch.setattr(runner, "delete_from_imagebed", lambda fn: calls.append(fn))
    monkeypatch.setattr(runner, "PREV_LINK", str(prev))

    runner._cleanup_removed(
        [{"name": "keep", "link": "https://keep.example.com/"}]
    )

    assert set(calls) == {"old.example.com.png", "gone.example.com.png"}
    assert "keep.example.com.png" not in calls


def test_cleanup_removed_case_insensitive(tmp_path, monkeypatch):
    """友链 URL 大小写差异不应导致误删/漏删。"""
    prev = tmp_path / "prev_link.json"
    _write_link(prev, ["Blog.Example.com"])  # 上一轮大写

    calls = []
    monkeypatch.setattr(runner, "delete_from_imagebed", lambda fn: calls.append(fn))
    monkeypatch.setattr(runner, "PREV_LINK", str(prev))

    # 本轮同 host 小写形式，应视为同一友链，不删
    runner._cleanup_removed(
        [{"name": "x", "link": "https://blog.example.com/"}]
    )
    assert calls == []


def test_cleanup_removed_no_prev_link_skips(tmp_path, monkeypatch):
    """未配置 PREV_LINK 时不删除任何图床文件。"""
    calls = []
    monkeypatch.setattr(runner, "delete_from_imagebed", lambda fn: calls.append(fn))
    monkeypatch.setattr(runner, "PREV_LINK", "")  # 空 → 跳过

    runner._cleanup_removed([{"name": "x", "link": "https://a.com/"}])
    assert calls == []


def test_cleanup_removed_missing_file_skips(tmp_path, monkeypatch):
    """PREV_LINK 指向不存在的文件时静默跳过。"""
    calls = []
    monkeypatch.setattr(runner, "delete_from_imagebed", lambda fn: calls.append(fn))
    monkeypatch.setattr(runner, "PREV_LINK", str(tmp_path / "nope.json"))

    runner._cleanup_removed([{"name": "x", "link": "https://a.com/"}])
    assert calls == []
