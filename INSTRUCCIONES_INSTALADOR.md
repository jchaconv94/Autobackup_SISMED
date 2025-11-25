# Guía para crear instalador de AutoBackup SISMED

## ¿Qué incluye el instalador?

Dos programas:
- **AutoBackup SISMED.exe** → Servicio que corre en segundo plano con icono en bandeja
- **AutoBackup Config.exe** → Interfaz para configurar (solo cuando necesites cambiar algo)

## Pasos para crear el instalador:

### 1. Crear los ejecutables
```powershell
.\crear_ejecutable.bat
```
Esto creará ambos archivos .exe en la carpeta `dist\`

### 2. Instalar Inno Setup
- Descarga: https://jrsoftware.org/isdl.php
- Instala Inno Setup 6

### 3. Compilar el instalador
1. Abre **Inno Setup Compiler**
2. File → Open → Selecciona `installer_script.iss`
3. Build → Compile
4. El instalador estará en: `installer_output\AutoBackup_SISMED_Setup.exe`

## ¿Cómo funciona para el usuario?

### Primera instalación:
1. Ejecuta `AutoBackup_SISMED_Setup.exe`
2. Al finalizar, se abre automáticamente **AutoBackup Config**
3. Usuario conecta a Drive (se abrirá navegador)
4. Usuario selecciona carpeta a monitorear
5. Usuario cierra la ventana

### Desde ese momento:
- **AutoBackup SISMED** se ejecuta automáticamente al iniciar Windows
- Aparece icono en bandeja del sistema (cerca del reloj)
- Clic derecho en icono:
  - "Abrir interfaz" → Para cambiar configuración
  - "Ver logs" → Ver actividad
  - "Salir" → Detener servicio

## Accesos directos creados:

En el menú inicio:
- **AutoBackup SISMED** → Inicia el servicio manualmente
- **Configurar AutoBackup** → Abre la interfaz de configuración

## Para distribuir:

Comparte solo: `AutoBackup_SISMED_Setup.exe`

El usuario NO necesita:
- ❌ Python instalado
- ❌ Librerías adicionales
- ❌ Archivos de configuración

Solo necesita:
- ✅ Windows 10/11
- ✅ Cuenta de Google Drive
- ✅ Ejecutar el instalador

## Notas importantes:

- Primera ejecución requiere configuración manual (2 minutos)
- Después funciona 100% automático
- El servicio inicia con Windows sin mostrar ventanas
- Icono en bandeja siempre visible para acceso rápido
