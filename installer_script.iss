[Setup]
AppName=AutoBackup SISMED
AppVersion=1.0
DefaultDirName={autopf}\AutoBackup SISMED
DefaultGroupName=AutoBackup SISMED
OutputDir=installer_output
OutputBaseFilename=AutoBackup_SISMED_Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin

[Files]
Source: "dist\AutoBackup SISMED.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\AutoBackup Config.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "settings.yaml"; DestDir: "{app}"; Flags: ignoreversion
Source: "client_secrets.json"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\AutoBackup SISMED"; Filename: "{app}\AutoBackup SISMED.exe"; Comment: "Servicio de respaldo automatico"
Name: "{group}\Configurar AutoBackup"; Filename: "{app}\AutoBackup Config.exe"; Comment: "Configurar carpetas y conexion"
Name: "{group}\Desinstalar AutoBackup SISMED"; Filename: "{uninstallexe}"
Name: "{commonstartup}\AutoBackup SISMED"; Filename: "{app}\AutoBackup SISMED.exe"; Comment: "Inicia automaticamente el servicio"; WorkingDir: "{app}"

[Run]
Filename: "{app}\AutoBackup Config.exe"; Description: "Configurar AutoBackup (primera vez)"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
