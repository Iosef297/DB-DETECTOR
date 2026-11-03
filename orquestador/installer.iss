; Instalador de DB-Detector Orquestador (Inno Setup 6).
; Instala por usuario (sin admin) en %LOCALAPPDATA%\Programs, asi funciona en
; cuentas no administradoras. Los datos de la app viven en ~/.orquestador y NO
; se borran al desinstalar.
;
; Uso: .\build-installer.ps1   (arma el .exe con PyInstaller y luego esto)

#define AppName "DB-Detector Orquestador"
#define AppExe "DB-Detector-Orquestador.exe"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{146987A1-248E-4A31-948C-231FF9F0F05E}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=IOSEF
DefaultDirName={autopf}\DB-Detector-Orquestador
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer_output
OutputBaseFilename=DB-Detector-Orquestador-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExe}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\DB-Detector-Orquestador\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Acceso normal: sin pedir admin (evita el UAC en cuentas de estudiante).
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--no-elevate"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--no-elevate"; Tasks: desktopicon
; Acceso elevado: "Liberar RAM" completo (vaciar standby list) necesita admin.
Name: "{autoprograms}\{#AppName} (administrador)"; Filename: "{app}\{#AppExe}"

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "--no-elevate"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// La ventana usa WebView2 (pywebview). Windows 11 lo trae; algunos Windows 10 no.
const WV2Key = '\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function HasWebView2(): Boolean;
var v: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node' + WV2Key, 'pv', v) and (v <> '') and (v <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM, 'SOFTWARE' + WV2Key, 'pv', v) and (v <> '') and (v <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, 'Software' + WV2Key, 'pv', v) and (v <> '') and (v <> '0.0.0.0'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var rc: Integer;
begin
  if (CurStep = ssPostInstall) and (not HasWebView2()) and (not WizardSilent()) then
    if MsgBox('Esta PC no tiene Microsoft Edge WebView2, necesario para abrir la ventana de la app.' + #13#10 +
              '¿Abrir la pagina de descarga ahora? (instala el "Evergreen Bootstrapper")',
              mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/', '', '', SW_SHOWNORMAL, ewNoWait, rc);
end;
