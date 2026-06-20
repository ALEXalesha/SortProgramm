[Setup]
AppName=Сортировщик загрузок
AppVersion=1.0
AppPublisher=Alexey
DefaultDirName={autopf}\Сортировщик загрузок
DefaultGroupName=Сортировщик загрузок
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\Сортировщик.exe
OutputDir=release
OutputBaseFilename=СортировщикЗагрузок-Setup
SetupIconFile=icon.ico
PrivilegesRequired=lowest
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
Source: "dist\Сортировщик.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "config.json"; DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "overrides.json"; DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Сортировщик загрузок"; Filename: "{app}\Сортировщик.exe"; IconFilename: "{app}\icon.ico"
Name: "{userdesktop}\Сортировщик загрузок"; Filename: "{app}\Сортировщик.exe"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\Сортировщик.exe"; Description: "Запустить Сортировщик загрузок"; Flags: nowait postinstall skipifsilent
