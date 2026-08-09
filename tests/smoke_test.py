#!/usr/bin/env python3
"""Smoke test the import and installed CLI command tree."""

import subprocess

from msm.config import Configs

Configs()
subprocess.run(["msm", "--help"], check=True)
subprocess.run(["msm", "sync", "--help"], check=True)
