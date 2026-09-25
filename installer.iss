[Setup]
AppName=Сортировщик загрузок
AppVersion=4.2.0
AppPublisher=Alexey
DefaultDirName={autopf}\Сортировщик загрузок
DefaultGroupName=Сортировщик загрузок
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\Сортировщик.exe
OutputDir=release
; Латиницей: GitHub выкидывает кириллицу из имён файлов выпуска (у 4.1.0 остались
; «-Setup.exe» и «-portable.zip»).
OutputBaseFilename=SortProgramm-4.2.0-setup
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
; config.json, overrides.json и my_rules.json принадлежат пользователю и
; установщиком не поставляются вовсе: раньше он подкладывал всем файлы
; автора — его папку загрузок, вынос 3D в его папку и двести ручных правил
; по именам его загрузок. Первый запуск без config.json — обычное дело
; (Config.load), окно запишет его само. Уже лежащие файлы установщик
; не трогает и так.
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Сортировщик загрузок"; Filename: "{app}\Сортировщик.exe"; IconFilename: "{app}\icon.ico"
Name: "{userdesktop}\Сортировщик загрузок"; Filename: "{app}\Сортировщик.exe"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\Сортировщик.exe"; Description: "Запустить Сортировщик загрузок"; Flags: nowait postinstall skipifsilent
