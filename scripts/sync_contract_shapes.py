#!/usr/bin/env python3
"""从总契约原文抽出 ATP 相关报文结构，落成入库副本 tests/fixtures/contract_shapes.json。

## 为什么要入库副本（而不是测试时现读契约）

契约的唯一事实源在 Hub 仓，本仓经 `__temp__/cicd_hub` 软链引用。但 `__temp__/` 在
.gitignore 里——**CI checkout 出来根本没有这个软链**，测试于是全部 skip：
绿着，什么都没测。而这套测试本来就是为了防「约定了但没人执行」，它自己却成了那个形态。
（Hub owner 2026-09-04 用 `git archive` 模拟 CI checkout 在其仓实测命中，ATP 侧同形。）

修法（承 Hub owner 提案）：**把「契约检查」与「副本是否同步」拆成两件事**
- 契约检查读**入库副本**，副本缺失 → assert 失败（副本缺失是仓的问题，应当红）；
- 「副本 vs 上游是否漂移」**单列一项，只有它可以 skip**——它检查的是同步状态，
  不是契约本身。拿不到上游时跳过它是合理的，但**不能因此把契约检查也一并跳掉**。

## 用法

    python3 scripts/sync_contract_shapes.py          # 重新抽取并写入副本
    python3 scripts/sync_contract_shapes.py --check  # 只比对不写（本地自查）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
CONTRACT = _ROOT / "__temp__" / "cicd_hub" / "docs" / "PatrolBox-通信与接口总契约.md"
FIXTURE = _ROOT / "tests" / "fixtures" / "contract_shapes.json"

# 抽哪些块：键 = 测试里用的名字，值 = 契约中该块前的标题正则
BLOCKS = {
    "atp_health": r"\*\*`GET /atp/health`\*\*",
    "atp_evaluations_submit": r"\*\*`POST /atp/evaluations`\*\*",
    "atp_evaluation_status": r"\*\*`GET /atp/evaluations/\{job_id\}`\*\*",
    "ci_callback": r"Authorization: Bearer <hub\.callback_token>",
}


def strip_jsonc(text: str) -> str:
    """去掉 // 注释，但不碰字符串内部（契约里的值含 `<分支; 通路1=...>` 这类文本）。"""
    out, in_str, esc, i = [], False, False, 0
    while i < len(text):
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == "/" and i + 1 < len(text) and text[i + 1] == "/":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def extract(md: str) -> dict:
    shapes = {}
    for name, pattern in BLOCKS.items():
        m = re.search(pattern + r".*?```jsonc?\n(.*?)```", md, re.S)
        if not m:
            raise SystemExit(f"契约里找不到 {name}（正则 {pattern!r}）——章节标题变了？")
        shapes[name] = json.loads(strip_jsonc(m.group(1)))
    return shapes


def render(shapes: dict) -> str:
    return json.dumps(shapes, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对不写，漂移则非零退出")
    args = ap.parse_args()

    if not CONTRACT.is_file():
        print(f"上游契约不可达: {CONTRACT}（__temp__/cicd_hub 软链未建）", file=sys.stderr)
        return 2
    payload = render(extract(CONTRACT.read_text(encoding="utf-8")))

    if args.check:
        if not FIXTURE.is_file():
            print(f"副本不存在: {FIXTURE}", file=sys.stderr)
            return 1
        if FIXTURE.read_text(encoding="utf-8") != payload:
            print("副本与上游契约已漂移 → python3 scripts/sync_contract_shapes.py",
                  file=sys.stderr)
            return 1
        print("副本与上游一致")
        return 0

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(payload, encoding="utf-8")
    print(f"已写入 {FIXTURE.relative_to(_ROOT)}（{len(shapes := extract(CONTRACT.read_text(encoding='utf-8')))} 个报文块）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
