@echo off
REM Set up Conda environment path
CALL C:\ProgramData\miniconda3\Scripts\activate.bat C:\CondaEnv\NAIPImagery

REM Run the Python script
python "\\hal-nas.hal.local\Backup\Software\Python Tools\Get2021NAIPImagery_Utah.py"

REM Keep the window open to display any output
pause