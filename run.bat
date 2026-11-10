@echo off
set "DIR=%~dp0"
set "PYTHONPATH=%DIR%src;%PYTHONPATH%"
python -m agentcut %*
