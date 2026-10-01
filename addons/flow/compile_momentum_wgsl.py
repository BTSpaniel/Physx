#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compile only the first-party terminal resident-cell gather/scatter shaders."""
import argparse
from pathlib import Path
from compile_scalar_wgsl import build, DEFAULT_SLANGC
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--slangc',type=Path,default=DEFAULT_SLANGC)
    build(parser.parse_args().slangc,component='momentum',names=('PrMomentumGatherCS','PrMomentumApplyCS'),prefix='PrMomentum',
          description='Terminal paired momentum exchange resident-cell admission and native halo scatter')
