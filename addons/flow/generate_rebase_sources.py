#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Append first-party coordinate operations to pinned upstream translation units.

Original NVIDIA source is included unchanged, retaining its own license. The
appendices need private state access; no copied layout or hand-edited vendor
file is used. Each upstream byte digest is checked before generating wrappers.
"""
from pathlib import Path
import hashlib

PINNED = {
    'nvflow/Sparse.cpp': ('8dc226e0bb55fc819d3cfb4cedc5a934045a65ae2e86969ec2d1ce8ef94ded43', 'rebase_sparse.h', ''),
    'nvflow/Summary.cpp': ('3c54cc23fc8f2449d2db3086abf1b2827d6573051dac3d342d46ad481362bbde', 'rebase_summary.h', ''),
    'nvflowext/Grid.cpp': ('e4084c8bd3b906199ec232f7e1cf4a7f2cfdb6cc41b6dde28037c6e885fe6a01', 'rebase_grid.h', ''),
    'nvflowext/EmitterSphere.cpp': ('5561e8299188176f00ae035254b6dd50298b1fcd54eb0f1aaf903efceca8c59f', 'rebase_history.h', 'PR_FLOW_REBASE_SPHERE'),
    'nvflowext/EmitterBox.cpp': ('4464864f300d648961502f40fe7e2ca37296ababac98092a586edd1a28afefa7', 'rebase_history.h', 'PR_FLOW_REBASE_BOX'),
    'nvflowext/EmitterMesh.cpp': ('a3e0cf59aa0fda34213e1cfe7b8522d60546caeef5e42ad1428eb3900be69423', 'rebase_history.h', 'PR_FLOW_REBASE_MESH'),
}


def generate(flow: Path, output: Path) -> dict[Path, Path]:
    staged = []
    for relative, (expected, appendix, define) in PINNED.items():
        source = flow / 'source' / relative
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError(f'Flow rebase upstream source differs from pinned input: {relative}')
        code = '// SPDX-License-Identifier: MIT\n// Generated first-party inclusion wrapper.\n'
        code += f'// Original source SHA256: {expected}\n'
        code += '#include <initializer_list>\n'
        code += f'#include "{source.as_posix()}"\n'
        if define:
            code += f'#define {define} 1\n'
        code += f'#include "{appendix}"\n'
        staged.append((source, output / source.name, code))
    output.mkdir(parents=True, exist_ok=True)
    for _, target, code in staged:
        if not target.exists() or target.read_text() != code:
            target.write_text(code, newline='\n')
    return {source: target for source, target, _ in staged}
