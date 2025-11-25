@echo off
echo Instalando PyInstaller...
call .venv\Scripts\pip.exe install pyinstaller

echo.
echo Creando ejecutable del SERVICIO (con icono en bandeja)...
call .venv\Scripts\pyinstaller.exe --name="AutoBackup SISMED" --onefile --windowed --add-data "settings.yaml;." AutoBackup_service.py

echo.
echo Creando ejecutable de CONFIGURACION (interfaz grafica)...
call .venv\Scripts\pyinstaller.exe --name="AutoBackup Config" --onefile --windowed --add-data "settings.yaml;." AutoBackup.py

echo.
echo ================================================
echo Ejecutables creados:
echo   - dist\AutoBackup SISMED.exe (servicio con icono en bandeja)
echo   - dist\AutoBackup Config.exe (para configurar)
echo ================================================
pause
