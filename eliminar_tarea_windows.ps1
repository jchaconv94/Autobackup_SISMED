# Script para ELIMINAR la tarea programada de inicio automático

$TaskName = "AutoBackup SISMED"

$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($ExistingTask) {
    Write-Host "Eliminando tarea '$TaskName'..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tarea eliminada exitosamente." -ForegroundColor Green
} else {
    Write-Host "La tarea '$TaskName' no existe." -ForegroundColor Cyan
}
