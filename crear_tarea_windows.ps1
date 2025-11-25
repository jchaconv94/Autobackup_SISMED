# Script para crear tarea programada de inicio automático en Windows
# Ejecutar este script como Administrador

$TaskName = "AutoBackup SISMED"
$PythonExe = "D:\Programas y apps creadas\AutoBackup\.venv\Scripts\pythonw.exe"
$ScriptPath = "D:\Programas y apps creadas\AutoBackup\AutoBackup_service.py"
$WorkingDir = "D:\Programas y apps creadas\AutoBackup"

# Verificar que los archivos existen
if (-not (Test-Path $PythonExe)) {
    Write-Host "ERROR: No se encuentra el Python ejecutable en: $PythonExe" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $ScriptPath)) {
    Write-Host "ERROR: No se encuentra el script en: $ScriptPath" -ForegroundColor Red
    exit 1
}

# Eliminar tarea existente si existe
$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($ExistingTask) {
    Write-Host "Eliminando tarea existente..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Crear acción
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory $WorkingDir

# Crear trigger (al iniciar sesión del usuario actual)
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Configuración adicional
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)  # Sin límite de tiempo

# Crear principal (usuario actual, sin privilegios elevados)
$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

# Registrar la tarea
try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Description "Monitoreo automatico de backups SISMED a Google Drive" `
        -Force
    
    Write-Host ""
    Write-Host "Tarea programada creada exitosamente!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Detalles:" -ForegroundColor Cyan
    Write-Host "  - Nombre: $TaskName"
    Write-Host "  - Se ejecutara al iniciar sesion de: $env:USERNAME"
    Write-Host "  - Python: $PythonExe"
    Write-Host "  - Script: $ScriptPath"
    Write-Host ""
    Write-Host "Para verificar: Abre 'Programador de tareas' y busca '$TaskName'" -ForegroundColor Yellow
    Write-Host "Para deshabilitar: ejecuta 'Disable-ScheduledTask -TaskName ""$TaskName""'" -ForegroundColor Yellow
    Write-Host "Para eliminar: ejecuta 'Unregister-ScheduledTask -TaskName ""$TaskName"" -Confirm:`$false'" -ForegroundColor Yellow
}
catch {
    Write-Host ""
    Write-Host "ERROR al crear la tarea: $_" -ForegroundColor Red
    exit 1
}
