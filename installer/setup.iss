; MCPAssistant Inno Setup 6/7 脚本
; 用于生成适合普通用户的一键安装 EXE 安装程序

#define MyAppName "MCP助手"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "定义"
#define MyAppExeName "MCPAssistant.exe"
#define MyAppId "{{8F2C41B9-72E5-46B9-B37D-97D8C61E8D90}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\MCPAssistant
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\dist
OutputBaseFilename=MCP助手-v1.0.0-win-x64-setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes
SetupIconFile=..\src\ui_qt\resources\branding\mcp_assistant.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
AppMutex=Local\MCPAssistant_SingleInstance_Mutex
CloseApplications=yes
RestartApplications=no
LicenseFile=LICENSE_FOR_SETUP.txt

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\MCPAssistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\NO-SALE-ADDENDUM.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\NOTICE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\QT_LGPL_COMPLIANCE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\PRIVACY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; 卸载时彻底移除用户开机自启注册表项（兼顾新项与旧项）
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "MCPAssistant"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "XiaozhiMcpApp"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
var
  ShouldPurgeUserData: Boolean;

const
  INVALID_FILE_ATTRIBUTES = $FFFFFFFF;

function GetFileAttributes(lpFileName: string): DWORD;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function FindFirstExistingAncestor(Path: string): string;
var
  Cur: string;
begin
  Cur := RemoveBackslashUnlessRoot(Path);
  while (Cur <> '') and (not DirExists(Cur)) do
  begin
    Cur := ExtractFileDir(Cur);
  end;
  Result := Cur;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  TargetDir, AncestorDir, ProbeFile: string;
  IsWritable: Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    // 若当前为管理员安装模式，允许受保护目录，由系统 UAC 保证权限
    if IsAdminInstallMode then
      Exit;

    TargetDir := WizardDirValue;
    AncestorDir := FindFirstExistingAncestor(TargetDir);
    if AncestorDir = '' then
      AncestorDir := ExtractFileDrive(TargetDir) + '\';

    // 生成安全临时探测文件验证当前目录真实写权限
    ProbeFile := AddBackslash(AncestorDir) + '.mcp_assistant_write_test_' + GetDateTimeString('yyyymmddhhnnss', '', '') + '.tmp';
    IsWritable := SaveStringToFile(ProbeFile, 'probe', False);

    if IsWritable then
    begin
      DeleteFile(ProbeFile);
    end
    else
    begin
      MsgBox(
        '当前安装位置需要管理员权限。' + #13#10#13#10 +
        '你可以返回安装模式页面，选择“为所有用户安装”并通过 Windows 权限确认；' + #13#10 +
        '也可以选择 D:\Apps\MCPAssistant、D:\MCPAssistant 等当前用户可写的目录。',
        mbError, MB_OK
      );
      Result := False;
    end;
  end;
end;

function InitializeUninstall(): Boolean;
var
  Prompt1, Prompt2: string;
begin
  Result := True;
  ShouldPurgeUserData := False;

  // 静默卸载一律不询问、不删除用户数据，完整保留
  if UninstallSilent then
    Exit;

  // 管理员模式或提权运行下，为避免误删其他 Windows 账号的数据，严格禁止触碰用户目录
  if IsAdminInstallMode or IsAdmin then
  begin
    MsgBox(
      '本软件采用“所有用户”或管理员模式安装。' + #13#10#13#10 +
      '为避免误删其他 Windows 账号的连接码和设置，本次卸载只删除程序文件，所有个人数据将被保留。',
      mbInformation, MB_OK
    );
    Exit;
  end;

  Prompt1 :=
    '是否同时删除 MCP助手 的全部个人数据？' + #13#10#13#10 +
    '包括：' + #13#10 +
    '• MCP 连接码' + #13#10 +
    '• 应用授权与设置' + #13#10 +
    '• 软件目录缓存' + #13#10 +
    '• 本地日志' + #13#10 +
    '• 已迁移的旧测试版数据' + #13#10#13#10 +
    '选择“否”将保留这些数据，重新安装后可以继续使用。';

  if MsgBox(Prompt1, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
  begin
    Prompt2 :=
      '删除后，已保存的连接码、应用授权、设置、日志和缓存都无法恢复。' + #13#10#13#10 +
      '同时还会删除已经迁移的旧版 XiaozhiMcpApp 配置数据。' + #13#10 +
      '确定永久删除吗？';

    if MsgBox(Prompt2, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      ShouldPurgeUserData := True;
    end;
  end;
end;

function SafeDeleteUserDataFolder(TargetDir, BaseDir: string): Boolean;
var
  NormTarget, NormBase, TargetName: string;
  Attr: DWORD;
begin
  Result := False;
  // 1. 拒绝空路径
  if (TargetDir = '') or (BaseDir = '') then
    Exit;

  // 2. 拒绝包含未解析变量符
  if (Pos('{', TargetDir) > 0) or (Pos('}', TargetDir) > 0) or
     (Pos('{', BaseDir) > 0) or (Pos('}', BaseDir) > 0) then
    Exit;

  // 3. 拒绝相对路径（必须包含有效盘符路径）
  if (Pos(':\', TargetDir) = 0) or (Pos(':\', BaseDir) = 0) then
    Exit;

  // 4. 严格边界检查：拒绝父级目录跳转
  if (Pos('..', TargetDir) > 0) or (Pos('..', BaseDir) > 0) then
    Exit;

  NormTarget := RemoveBackslashUnlessRoot(TargetDir);
  NormBase := RemoveBackslashUnlessRoot(BaseDir);

  // 5. 拒绝盘符根目录
  if (Length(NormTarget) <= 3) or (Length(NormBase) <= 3) then
    Exit;

  // 若目录原本已不存在，视为已清理成功
  if not DirExists(NormTarget) then
  begin
    Result := True;
    Exit;
  end;

  // 6. 拒绝根目录与基准目录本身
  if CompareText(NormTarget, NormBase) = 0 then
    Exit;

  // 7. 目标必须是基准目录的直接子目录
  if CompareText(ExtractFileDir(NormTarget), NormBase) <> 0 then
    Exit;

  // 8. 最后一级目录名必须精确完全等于 MCPAssistant 或 XiaozhiMcpApp
  TargetName := ExtractFileName(NormTarget);
  if (CompareText(TargetName, 'MCPAssistant') <> 0) and
     (CompareText(TargetName, 'XiaozhiMcpApp') <> 0) then
    Exit;

  // 9. 检测 Windows 符号链接与重解析点 (Reparse Point)，避免沿链接误删
  Attr := GetFileAttributes(NormTarget);
  if (Attr <> INVALID_FILE_ATTRIBUTES) and ((Attr and FILE_ATTRIBUTE_REPARSE_POINT) <> 0) then
  begin
    Exit;
  end;

  // 10. 执行递归删除
  Result := DelTree(NormTarget, True, True, True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  UserAppData, UserLocalData: string;
  FailedList: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if ShouldPurgeUserData and (not IsAdminInstallMode) and (not IsAdmin) and (not UninstallSilent) then
    begin
      try
        UserAppData := ExpandConstant('{userappdata}');
        UserLocalData := ExpandConstant('{localappdata}');
      except
        UserAppData := '';
        UserLocalData := '';
      end;

      // 失败关闭检查：若路径为空或包含未展开花括号，终止清理，不崩溃
      if (UserAppData = '') or (UserLocalData = '') or
         (Pos('{', UserAppData) > 0) or (Pos('{', UserLocalData) > 0) then
      begin
        if not UninstallSilent then
        begin
          MsgBox(
            '无法安全定位当前用户的配置目录，为防止误删数据，已跳过数据清除。' + #13#10 +
            '如有需要，你可以在稍后手动删除个人数据。',
            mbInformation, MB_OK
          );
        end;
        Exit;
      end;

      FailedList := '';

      if not SafeDeleteUserDataFolder(AddBackslash(UserAppData) + 'MCPAssistant', UserAppData) then
        FailedList := FailedList + '  - %APPDATA%\MCPAssistant' + #13#10;

      if not SafeDeleteUserDataFolder(AddBackslash(UserLocalData) + 'MCPAssistant', UserLocalData) then
        FailedList := FailedList + '  - %LOCALAPPDATA%\MCPAssistant' + #13#10;

      if not SafeDeleteUserDataFolder(AddBackslash(UserAppData) + 'XiaozhiMcpApp', UserAppData) then
        FailedList := FailedList + '  - %APPDATA%\XiaozhiMcpApp' + #13#10;

      if not SafeDeleteUserDataFolder(AddBackslash(UserLocalData) + 'XiaozhiMcpApp', UserLocalData) then
        FailedList := FailedList + '  - %LOCALAPPDATA%\XiaozhiMcpApp' + #13#10;

      if (FailedList <> '') and (not UninstallSilent) then
      begin
        MsgBox(
          '主体程序已完成卸载，但以下个人数据目录未能完全清除（可能正被占用）：' + #13#10#13#10 +
          FailedList + #13#10 +
          '如有需要，你可以在稍后手动删除这些文件夹。',
          mbInformation, MB_OK
        );
      end;
    end;
  end;
end;
