[Setup]
AppName=Сортировщик загрузок
AppVersion=1.5
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
; Если старая версия запущена, она держит Сортировщик.exe и обновление «поверх»
; падает с «DeleteFile код 5». Restart Manager сам находит процесс по файловому
; дескриптору (кириллица в имени не мешает) и force принудительно его завершает.
CloseApplications=force
RestartApplications=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
Source: "dist\Сортировщик.exe"; DestDir: "{app}"; Flags: ignoreversion
; rules.json — правила раскладки, их поставляет программа: обновляем всегда,
; иначе новые категории не доедут до уже установленной копии.
Source: "rules.json"; DestDir: "{app}"; Flags: ignoreversion
; config.json и overrides.json принадлежат пользователю (пути, ручные правила) —
; при обновлении не трогаем.
Source: "config.json"; DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "overrides.json"; DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Сортировщик загрузок"; Filename: "{app}\Сортировщик.exe"; IconFilename: "{app}\icon.ico"
Name: "{userdesktop}\Сортировщик загрузок"; Filename: "{app}\Сортировщик.exe"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\Сортировщик.exe"; Description: "Запустить Сортировщик загрузок"; Flags: nowait postinstall skipifsilent
