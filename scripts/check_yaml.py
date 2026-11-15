#!/usr/bin/env python3
"""快速校验 YAML 文件语法：scripts/e2e 与 manifests 下的所有文档。

用法：python3 scripts/check_yaml.py [路径...]
不带参数时校验 scripts/e2e/*.yaml 与 manifests/*.yaml。
"""

import glob
import sys

import yaml

def main(paths) -> int:
    targets = []
    for path in paths:
        targets.extend(sorted(glob.glob(path)))
    if not targets:
        targets = sorted(
            glob.glob("scripts/e2e/*.yaml")
            + glob.glob("scripts/e2e/*.yml")
            + glob.glob("manifests/*.yaml")
        )
    failed = 0
    for name in targets:
        try:
            with open(name, encoding="utf-8") as handle:
                docs = list(yaml.safe_load_all(handle))
            print("{0}: OK ({1} doc(s))".format(name, len(docs)))
        except yaml.YAMLError as error:
            print("{0}: YAML 错误 -> {1}".format(name, error))
            failed += 1
    if failed:
        print("共 {} 个文件校验失败".format(failed))
        return 1
    print("全部 {} 个文件校验通过".format(len(targets)))
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
