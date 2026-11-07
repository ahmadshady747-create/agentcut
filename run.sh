#!/bin/sh
# AgentCut single run command. Usage: ./run.sh serve | validate <json> |
# render <json> <mp4> | preview <json> <dir> | probe <file> |
# poster <json> <png> [seconds] | contact-sheet <video> <out.png> [project.json] |
# produce <brief.json> <mp4> |
# produce-explainer <brief.json> <mp4> |
# produce-variants <brief.json> <dir> | package <json> <outdir>
cd "$(dirname "$0")"
PYTHONPATH=src exec python3 -m agentcut "$@"
