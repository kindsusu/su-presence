# -*- coding: utf-8 -*-
"""테스트 묶음이 실행 위치에 의존하지 않는지 본다 — 네트워크를 쓰지 않는다.

두 모듈이 `from tests import ...` 를 쓰면서 **저장소 루트를 경로에 넣지 않아**,
저장소 루트에서 실행할 때만 cwd 덕에 우연히 동작했다. 설치된 스킬 폴더를 절대경로로
가리켜 돌리면 `ModuleNotFoundError: No module named 'tests'` 로 21건이 통째로 빠졌다 —
그런데 **나머지가 통과하니 성공처럼 보였다.** 그게 이 파일이 존재하는 이유다.

실행: python -m unittest discover tests
"""
import ast
import io
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
# 이 검사가 하위 프로세스로 돌리는 대상. 이 파일 자신은 이 패턴에 안 걸려 재귀하지 않는다.
PROBE_PATTERN = "test_*_reliability.py"


def modules_importing_tests_package():
    """`tests` 패키지를 import 하는 테스트 모듈 목록."""
    found = []
    for fn in sorted(os.listdir(TESTS)):
        if not (fn.startswith("test_") and fn.endswith(".py")):
            continue
        with io.open(os.path.join(TESTS, fn), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "tests":
                found.append(fn)
                break
    return found


def run_discovery(cwd):
    """주어진 cwd 에서 하위 프로세스로 수집해 (실행 수, stderr) 를 돌려준다."""
    out = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", TESTS, "-p", PROBE_PATTERN],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    ran = -1
    for line in (out.stderr or "").splitlines():
        if line.startswith("Ran ") and "test" in line:
            ran = int(line.split()[1])
    return ran, (out.stderr or "")


class TestEveryModuleCanFindItsImports(unittest.TestCase):
    """`tests` 를 import 하는 모듈은 저장소 루트를 스스로 경로에 넣어야 한다."""

    def test_modules_that_import_tests_also_insert_the_root(self):
        offenders = []
        for fn in modules_importing_tests_package():
            with io.open(os.path.join(TESTS, fn), encoding="utf-8") as fh:
                src = fh.read()
            if not ("sys.path.insert(0, str(ROOT))" in src
                    or "sys.path.insert(0, ROOT)" in src):
                offenders.append(fn)
        self.assertEqual(
            offenders, [],
            "`from tests import ...` 를 쓰면서 저장소 루트를 경로에 안 넣는다 "
            "— 저장소 루트 밖에서 실행하면 import 가 깨진다: %s" % offenders)

    def test_at_least_one_module_actually_imports_the_package(self):
        """대상이 0개면 위 검사가 아무것도 안 지키면서 통과한다."""
        self.assertNotEqual(modules_importing_tests_package(), [])


class TestDiscoveryWorksFromAnotherDirectory(unittest.TestCase):
    """설치된 스킬처럼 다른 위치에서 절대경로로 돌려도 같은 수가 나와야 한다."""

    def test_collected_count_is_the_same_from_a_foreign_cwd(self):
        n_here, _ = run_discovery(ROOT)
        n_away, err = run_discovery(os.path.dirname(ROOT))
        self.assertGreater(n_here, 0, "저장소 루트에서 수집이 안 됐다")
        self.assertNotIn("ModuleNotFoundError", err)
        self.assertEqual(
            n_away, n_here,
            "실행 위치에 따라 수집된 테스트 수가 다르다 (루트 %d, 다른 위치 %d). "
            "import 가 조용히 실패하면 나머지가 통과해 **성공처럼 보인다.**"
            % (n_here, n_away))


if __name__ == "__main__":
    unittest.main()
