@echo off
REM Set up Conda environment path
CALL C:\ProgramData\miniconda3\Scripts\activate.bat C:\CondaEnv\HMStoSSA
REM Run the Python script
C:\CondaEnv\HMStoSSA\python.exe "\\hal-nas.hal.local\Backup\Software\Python Tools\HMS_to_SSA_Conversion.py"

REM Keep the window open to display any output
pause