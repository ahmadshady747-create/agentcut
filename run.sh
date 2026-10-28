#!/bin/sh
# AgentCut single run command. Usage: ./run.sh serve | validate <json> |
# render <json> <mp4> | preview <json> <dir> | probe <file> |
# poster <json> <png> [seconds]
cd "$(dirname "$0")"
PYTHONPATH=src exec python3 -m agentcut "$@"
