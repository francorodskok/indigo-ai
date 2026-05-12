@echo off
REM Lanza el slack_listener al iniciar sesion, minimizado, con logs a archivo.
REM Para autostart: copiar a la carpeta shell:startup del usuario.

cd /d "C:\Users\franc\Indigo-AI"

REM Crear carpeta de logs si no existe
if not exist "pipeline\state\logs" mkdir "pipeline\state\logs"

REM Arrancar en ventana minimizada con stdout/stderr a log timestamped
set LOGFILE=pipeline\state\logs\slack_listener_%date:~6,4%-%date:~3,2%-%date:~0,2%.log
start "Indigo Slack Listener" /MIN cmd /c ""C:\Users\franc\AppData\Local\Programs\Python\Python313\python.exe" -m pipeline.social.slack_listener >> "%LOGFILE%" 2>&1"
