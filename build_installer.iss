[Setup]
; アプリケーションの基本情報
AppName=SubtitLocar
AppVersion=0.2
AppPublisher=SubtitLocar Project
; インストール先を C:\Users\[ユーザー名]\AppData\Local\SubtitLocar に指定
DefaultDirName={localappdata}\SubtitLocar
DefaultGroupName=SubtitLocar
; インストーラーの出力先ディレクトリとファイル名
OutputDir=D:\Locally_Translated_Subtitle_Project\installer
OutputBaseFilename=SubtitLocar_Setup_v0.2
; 圧縮アルゴリズムの設定
Compression=lzma2/max
SolidCompression=yes
; インストーラーのアイコンとアンインストーラーのアイコン
SetupIconFile=D:\Locally_Translated_Subtitle_Project\subtitlocar.ico
UninstallDisplayIcon={app}\main.exe
; 管理者権限を要求しない（AppData\Localへのインストールのため）
PrivilegesRequired=lowest

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作成する"; GroupDescription: "追加のショートカット:"

[Files]
; Nuitkaでビルドした dist_folder\main.dist フォルダの中身をすべてインストール先にコピー
Source: "D:\Locally_Translated_Subtitle_Project\dist_folder\main.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; スタートメニューのショートカット
Name: "{group}\SubtitLocar"; Filename: "{app}\main.exe"; IconFilename: "{app}\subtitlocar.ico"
; デスクトップのショートカット (オプション)
Name: "{autodesktop}\SubtitLocar"; Filename: "{app}\main.exe"; IconFilename: "{app}\subtitlocar.ico"; Tasks: desktopicon

[Run]
; インストール完了後にアプリを起動するオプション
Filename: "{app}\main.exe"; Description: "SubtitLocar を起動する"; Flags: nowait postinstall skipifsilent
