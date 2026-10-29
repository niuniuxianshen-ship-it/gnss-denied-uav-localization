"""只做本地检查；模拟档案仅在内存，不读取真实凭据或使用网络。"""
import ast
from contextlib import ExitStack
import copy
import http.client
import importlib.util
import io
import json
from pathlib import Path
import socket
import sys
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("repeat_runner_under_check", ROOT / "run_repeat.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)  # New runner definitions only; no old runner imports.


class MemoryArchive:
    """Path adapters for test-only input copies and archives; no temporary disk writes."""
    def __init__(self):
        self.root = ROOT / "_仅内存_离线检查"
        self.files = {}
        self.dirs = {self.root, self.root / "请求副本"}
        for name in ("配置.json", "六项任务关联_管理用.json", "使用与核查说明.md",
                     "请求副本/请求_H03_V04.txt", "请求副本/请求_H04_V04.txt"):
            self.files[self.root / name] = (ROOT / name).read_bytes()

    def local(self, path):
        return Path(path).is_relative_to(self.root)

    def install(self, stack):
        original = {name: getattr(Path, name) for name in
                    ("read_bytes", "exists", "is_file", "is_symlink", "mkdir", "open")}
        def read_bytes(path):
            if not self.local(path):
                return original["read_bytes"](path)
            if path not in self.files:
                raise FileNotFoundError(str(path))
            return self.files[path]
        def exists(path):
            return path in self.files or path in self.dirs if self.local(path) else original["exists"](path)
        def is_file(path):
            return path in self.files if self.local(path) else original["is_file"](path)
        def is_symlink(path):
            return False if self.local(path) else original["is_symlink"](path)
        def mkdir(path, mode=0o777, parents=False, exist_ok=False):
            if not self.local(path):
                raise AssertionError("离线测试禁止创建真实目录")
            if path in self.files or path in self.dirs:
                if exist_ok and path in self.dirs:
                    return
                raise FileExistsError(str(path))
            if path.parent not in self.dirs:
                raise FileNotFoundError(str(path.parent))
            self.dirs.add(path)
        owner = self
        class Writer(io.BytesIO):
            def __init__(self, path):
                super().__init__()
                self.path = path
            def close(self):
                if not self.closed:
                    owner.files[self.path] = self.getvalue()
                super().close()
        def open_file(path, mode="r", *args, **kwargs):
            if not self.local(path):
                return original["open"](path, mode, *args, **kwargs)
            if mode == "xb":
                if path in self.files or path in self.dirs:
                    raise FileExistsError(str(path))
                if path.parent not in self.dirs:
                    raise FileNotFoundError(str(path.parent))
                self.files[path] = b""
                return Writer(path)
            if mode == "rb":
                return io.BytesIO(read_bytes(path))
            raise AssertionError("离线内存文件不支持模式：" + mode)
        for name, func in (("read_bytes", read_bytes), ("exists", exists),
                           ("is_file", is_file), ("is_symlink", is_symlink),
                           ("mkdir", mkdir), ("open", open_file)):
            stack.enter_context(patch.object(Path, name, func))


def rejected(call):
    try:
        call()
    except (ValueError, FileNotFoundError, FileExistsError):
        return
    raise AssertionError("预期拒绝继续，但没有拒绝")


def main():
    results = []
    original_paths = [
        run.PROJECT / "依据记录H批次对照准备_v0.1/请求_H03_V04.txt",
        run.PROJECT / "依据记录H批次对照准备_v0.1/请求_H04_V04.txt",
        run.PROJECT / "依据记录H批次执行_v0.1/执行关联.json",
        run.PROJECT / "依据记录H批次执行_v0.1/run_checks.py",
        run.PROJECT / "关系记录新材料执行入口_v0.1/run_comparison.py",
        run.PROJECT / "关系记录新材料执行入口_v0.1/使用与离线核查说明.md"
    ]
    before = {str(path): path.read_bytes() for path in original_paths}
    assert not (ROOT / "运行档案").exists() and not (ROOT / "本批冻结登记.json").exists()
    def forbidden(*args, **kwargs):
        raise AssertionError("禁止读取真实凭据或使用真实网络")
    with ExitStack() as guard:
        guard.enter_context(patch.object(run, "existing_key", side_effect=forbidden))
        guard.enter_context(patch.object(run, "post_once", side_effect=forbidden))
        guard.enter_context(patch.object(http.client, "HTTPSConnection", side_effect=forbidden))
        for name in ("socket", "create_connection", "getaddrinfo"):
            guard.enter_context(patch.object(socket, name, side_effect=forbidden))

        config, mapping, bodies = run.check()
        assert [task["task_id"] for task in mapping["tasks"]] == run.ORDER
        assert len(set(run.ORDER)) == 6
        for material in ("H03", "H04"):
            group = [t for t in mapping["tasks"] if t["material_id"] == material]
            assert len(group) == 3
            assert len({bodies[t["task_id"]] for t in group}) == 1
            source = (run.PROJECT / run.SOURCE_REQUESTS[material]).read_bytes()
            for task in group:
                body = json.loads(bodies[task["task_id"]])
                assert set(body) == set(config["settings"]) | {"messages"}
                assert body["messages"] == [{"role": "user", "content": source.decode("utf-8")}]
                assert task["task_id"] not in body["messages"][0]["content"]
        results.append("正常check、六项固定顺序、同材料三项消息和载荷一致、单条user白名单：通过")

        state = MemoryArchive()
        changed = state.root / "请求副本/请求_H03_V04.txt"
        state.files[changed] += b"\nOFFLINE_MUTATION"
        with ExitStack() as stack:
            state.install(stack)
            rejected(lambda: run.check(state.root))
        results.append("内存副本哈希变化时拒绝：通过；历史原件未改")

        rejected(lambda: run.execute(False))
        state = MemoryArchive()
        with ExitStack() as stack:
            state.install(stack)
            rejected(lambda: run.execute(True, "0" * 64, state.root))
            assert state.root / "运行档案" not in state.dirs
        results.append("无显式执行开关、无本批冻结时拒绝且不读凭据：通过")

        target = ROOT / "请求副本/请求_H03_V04.txt"
        saved = target.read_bytes()
        rejected(lambda: run.save(target, b"FORBIDDEN_OVERWRITE"))
        assert target.read_bytes() == saved
        results.append("真实文件独占写入拒绝覆盖，已有请求副本字节不变：通过")

        state = MemoryArchive()
        directory = state.root / "运行档案"
        state.dirs.add(directory)
        state.files[directory / "保护标记.txt"] = b"OFFLINE_KEEP"
        files_before, dirs_before = dict(state.files), set(state.dirs)
        with ExitStack() as stack:
            state.install(stack)
            rejected(lambda: run.execute(True, None, state.root))
        assert state.files == files_before and state.dirs == dirs_before
        results.append("已有档案阻止执行或续跑，原内容不变：通过（内存模拟）")

        state = MemoryArchive()
        calls = []
        def fail_first(endpoint, body, content_type, key, timeout, archive, meta):
            calls.append(archive.name)
            raise OSError("OFFLINE_INJECTED_FAILURE")
        with ExitStack() as stack:
            state.install(stack)
            stack.enter_context(patch.object(run, "verify_freeze", return_value={"sha256": None}))
            stack.enter_context(patch.object(run, "existing_key", return_value="OFFLINE_PLACEHOLDER"))
            stack.enter_context(patch.object(run, "post_once", side_effect=fail_first))
            rejected(lambda: run.execute(True, None, state.root))
            summary = run.load_json(state.root / "运行档案/summary.json")
            meta = run.load_json(state.root / "运行档案" / run.ORDER[0] / "metadata.json")
            assert calls == [run.ORDER[0]] and summary["attempted"] == [run.ORDER[0]]
            assert summary["completed"] == [] and summary["unexecuted"] == run.ORDER[1:]
            assert summary["record_kind"] == meta["record_kind"] == "offline_check"
            assert summary["actual_api_calls"] == 0 and summary["freeze_sha256"] is None
            assert set(meta["missing_artifacts"]) == {"response.json", "output.txt"}
            assert not any((state.root / "运行档案" / task) in state.dirs for task in run.ORDER[1:])
            snapshot = dict(state.files)
            rejected(lambda: run.execute(True, None, state.root))
            assert state.files == snapshot and calls == [run.ORDER[0]]
        results.append("首项本地技术故障只尝试1项、后5项未执行、保留失败信息且再次执行被拒：通过")

        state = MemoryArchive()
        calls = []
        def completed_stub(endpoint, body, content_type, key, timeout, archive, meta):
            calls.append(archive.name)
            meta.update(http_status=200, response_content_type="application/json", response_complete=True)
            run.save_json(archive / "response.json", {
                "model": "OFFLINE_STUB_NOT_API_MODEL",
                "choices": [{"message": {"role": "assistant", "content": "离线技术桩；没有评分内容"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            })
        with ExitStack() as stack:
            state.install(stack)
            stack.enter_context(patch.object(run, "verify_freeze", return_value={"sha256": None}))
            stack.enter_context(patch.object(run, "existing_key", return_value="OFFLINE_PLACEHOLDER"))
            stack.enter_context(patch.object(run, "post_once", side_effect=completed_stub))
            run.execute(True, None, state.root)
            summary = run.load_json(state.root / "运行档案/summary.json")
            assert calls == run.ORDER and summary["completed"] == run.ORDER
            for task in run.ORDER:
                for name in ("attempt.json", "request.json", "response.json", "output.txt", "metadata.json"):
                    assert (state.root / "运行档案" / task / name) in state.files
            snapshot = dict(state.files)
            rejected(lambda: run.execute(True, None, state.root))
            assert state.files == snapshot and calls == run.ORDER
        results.append("六项本地成功桩完整归档；内容格式不由入口裁定、不重试；再次执行被拒：通过")

        def functions(path):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            return {node.name: ast.dump(node, include_attributes=False) for node in tree.body
                    if isinstance(node, ast.FunctionDef)}
        old = functions(original_paths[4])
        new = functions(ROOT / "run_repeat.py")
        names = ("now", "sha", "encoded", "save", "save_json", "require",
                 "payload", "post_once", "classify_completion")
        assert all(old[name] == new[name] for name in names)
        old_keys = functions(original_paths[3])
        assert old_keys["existing_key"] == new["existing_key"]
        results.append("9项通用函数及既有凭据函数AST一致；不导入旧入口：通过")

    assert all(Path(path).read_bytes() == raw for path, raw in before.items())
    assert not (ROOT / "运行档案").exists() and not (ROOT / "本批冻结登记.json").exists()
    print(json.dumps({
        "record_kind": "offline_check", "checked_at": run.now(), "checks": results,
        "all_passed": True, "existing_inputs_unchanged": len(before),
        "real_api_calls": 0, "real_credentials_read": False, "network_used": False,
        "temporary_disk_files": 0, "simulated_archives": "仅内存；退出即释放，不是API结果",
        "frozen": False
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
