@echo off
REM Lanza el slack_listener al iniciar sesion.
REM Para que arranque automaticamente: copiar este .bat (o un acceso directo)
REM a la carpeta de Inicio del usuario:
REM   shell:startup
REM (Win+R, escribir shell:startup, click derecho dentro, Pegar acceso directo)

cd /d "C:\Users\franc\Indigo-AI"
"C:\Users\franc\AppData\Local\Programs\Python\Python313\python.exe" -m pipeline.social.slack_listener
