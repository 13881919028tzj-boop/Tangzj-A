@echo off
setlocal
cd /d "%~dp0\.."
if not exist logs mkdir logs
python -m streamlit run app.py
endlocal
