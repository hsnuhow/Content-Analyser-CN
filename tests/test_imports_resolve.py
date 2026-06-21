# -*- coding: utf-8 -*-
"""跨模組 import 解析檢查（回歸守衛）。

對每個服務的本地模組，驗證所有 `from <本地模組> import <名稱>` 的 <名稱> 確實在該模組
頂層定義或 import 進來。

為何需要：`py_compile` 不會抓「from X import Y 但 X 根本沒有 Y」——尤其**延遲 import**
（寫在函式/route 內的 import），靜態語法檢查掃不到，只有執行到那行才 ImportError。
2026-06 就因重構把 SITE_TEMPLATES 搬離 crawler、MAIN_CONTENT_SELECTORS 搬到 dom_extract 後，
image_extract.py 仍 `from crawler import SITE_TEMPLATES, MAIN_CONTENT_SELECTORS`，導致
`/api/extract-images` 一觸發就 500。此測試在每次跑測試時一次抓出這類跨模組 import 回歸。

本地無 pytest 也能跑：python3 tests/test_imports_resolve.py
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICES = ['crawler-service', 'analysis-service', 'search-extent', 'app']


def _defined_names(path):
    """模組頂層定義或 import 進來、可被 `from mod import X` 取得的名稱集合。"""
    tree = ast.parse(open(path, encoding='utf-8').read())
    names = set()
    for n in tree.body:                       # 只看頂層（module scope）
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            for tg in n.targets:
                if isinstance(tg, ast.Name):
                    names.add(tg.id)
                elif isinstance(tg, (ast.Tuple, ast.List)):
                    for e in tg.elts:
                        if isinstance(e, ast.Name):
                            names.add(e.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
        elif isinstance(n, ast.Import):
            for a in n.names:
                names.add((a.asname or a.name).split('.')[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                names.add(a.asname or a.name)
    return names


def _check_service(svc):
    d = os.path.join(ROOT, svc)
    pyfiles = {f[:-3]: os.path.join(d, f) for f in os.listdir(d) if f.endswith('.py')}
    local = set(pyfiles)
    defcache = {}
    problems = []
    for mod, path in pyfiles.items():
        for n in ast.walk(ast.parse(open(path, encoding='utf-8').read())):
            # 只查「本地模組」的絕對 from-import（level==0）；第三方/標準庫不查。
            if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module in local:
                if n.module not in defcache:
                    defcache[n.module] = _defined_names(pyfiles[n.module])
                for a in n.names:
                    if a.name != '*' and a.name not in defcache[n.module]:
                        problems.append(
                            f"{svc}/{mod}.py:{n.lineno} "
                            f"`from {n.module} import {a.name}` → {n.module} 沒有 {a.name}")
    return problems


def test_all_cross_module_imports_resolve():
    problems = []
    for svc in SERVICES:
        if os.path.isdir(os.path.join(ROOT, svc)):
            problems += _check_service(svc)
    assert not problems, "跨模組 import 解析失敗（可能是重構搬走名稱後忘了改 import）：\n" + \
        "\n".join(problems)


if __name__ == '__main__':
    test_all_cross_module_imports_resolve()
    print("✅ test_imports_resolve: 所有服務的跨本地模組 import 都解析得到")
