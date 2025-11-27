# AutoBackup SISMED - Guía de uso

## Hay DOS versiones:

### 1. AutoBackup.py (Con interfaz gráfica)
- Úsala para configuración inicial
- Ver logs en tiempo real
- Cambiar configuración

**Ejecutar:**
```powershell
& "D:/Programas y apps creadas/AutoBackup/.venv/Scripts/python.exe" "AutoBackup.py"
```

### 2. AutoBackup_service.py (Servicio en segundo plano - SIN VENTANA)
- Funciona completamente oculto
- No muestra ventanas
- Logs en archivo: `autobackup_service.log`

**Ejecutar:**
```powershell
& "D:/Programas y apps creadas/AutoBackup/.venv/Scripts/pythonw.exe" "AutoBackup_service.py"
```

## Configuración recomendada:

### Paso 1: Configuración inicial (UNA SOLA VEZ)
```powershell
# Ejecuta la versión CON interfaz
& ".venv/Scripts/python.exe" "AutoBackup.py"
```
- Conecta a Drive
- Selecciona carpeta a monitorear
- Configura nombre de carpeta en Drive
- Cierra la aplicación

### Paso 2: Inicio automático del servicio
- **Si instalaste con `AutoBackup_SISMED_Setup.exe`:** el instalador coloca un acceso directo en la carpeta de Inicio de Windows, por lo que `AutoBackup SISMED.exe` se ejecutará solo al prender la PC. No necesitas programar tareas.
- **Si estás ejecutando directamente desde el repositorio (sin instalador):** puedes seguir usando los scripts `eliminar_tarea_windows.ps1` y `crear_tarea_windows.ps1` para registrar una tarea programada manualmente.

### Paso 3: Reinicia tu PC
- El servicio iniciará automáticamente (por el acceso directo en Inicio o la tarea manual, según tu caso)
- NO verás ninguna ventana
- Funcionará en segundo plano

## Ver logs del servicio:
```powershell
Get-Content autobackup_service.log -Tail 20 -Wait
```

## Detener el servicio:
```powershell
# Opción 1: Desde el Administrador de tareas
# Busca "pythonw.exe" y finalízalo

# Opción 2: Deshabilitar inicio automático
Disable-ScheduledTask -TaskName "AutoBackup SISMED"
```

## Cambiar configuración:
1. Ejecuta la versión GUI: `python AutoBackup.py`
2. Cambia lo que necesites
3. Cierra la aplicación
4. El servicio usará la nueva configuración en el próximo reinicio
