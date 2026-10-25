; Same Page for Windows — per-user installer (no administrator rights needed).
; Built by windows/build_windows.py with makensis: -DVERSION=x.y.z -DOUTFILE=out/...exe
Unicode true
ManifestDPIAware true
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!ifndef OUTFILE
  !define OUTFILE "out/SamePage-setup.exe"
!endif
!define APP "Same Page"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\SamePage"
!define PYW "$INSTDIR\engine\work\runtime\python\pythonw.exe"

Name "${APP}"
Caption "${APP} ${VERSION} 安装"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\Same Page"
InstallDirRegKey HKCU "${UNINSTKEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor lzma
SetCompressorDictSize 32
BrandingText "${APP} ${VERSION}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APP}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileDescription" "${APP} 安装程序"
VIAddVersionKey "LegalCopyright" "Same Page"

!define MUI_ICON "stage/samepage.ico"
!define MUI_UNICON "stage/samepage.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "安装 Same Page"
!define MUI_WELCOMEPAGE_TEXT "每一句，都跟得上。$\r$\n$\r$\nSame Page 用本机 AI 把英语、俄语或混合的声音实时变成原文字幕和中文译文。识别和翻译都在这台电脑上完成，不上传，不需要账号。$\r$\n$\r$\n安装约需 2.2 GB 磁盘空间，不需要管理员权限。"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "现在打开 Same Page"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApp

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

; Same Page processes run from the install folder; stop them before files are replaced.
!macro StopRunning
  ; PowerShell's exit code is the number of processes running from the install folder.
  nsExec::ExecToStack `powershell -NoProfile -ExecutionPolicy Bypass -Command "$$d='$INSTDIR'; exit @(Get-Process | Where-Object { $$_.Path -and $$_.Path.StartsWith($$d, 'OrdinalIgnoreCase') }).Count"`
  Pop $0
  Pop $1
  ${If} $0 != "error"
  ${AndIf} $0 != "timeout"
  ${AndIf} $0 != 0
    MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "Same Page 正在运行。请先关闭它的窗口（正在翻译时会先保存）。$\r$\n$\r$\n点“确定”继续，安装程序会结束残留的 Same Page 进程。" IDOK +2
    Abort
    nsExec::Exec `powershell -NoProfile -ExecutionPolicy Bypass -Command "$$d='$INSTDIR'; Get-Process | Where-Object { $$_.Path -and $$_.Path.StartsWith($$d, 'OrdinalIgnoreCase') } | Stop-Process -Force"`
    Pop $1
    Sleep 1500
  ${EndIf}
!macroend

Function LaunchApp
  SetOutPath "$INSTDIR"
  Exec '"${PYW}" "$INSTDIR\launcher.pyw"'
FunctionEnd

Section "Same Page" Main
  SectionIn RO
  !insertmacro StopRunning
  ; Replace the program cleanly; user records live in %LOCALAPPDATA%\Same Page and are untouched.
  RMDir /r "$INSTDIR\engine"
  SetOutPath "$INSTDIR"
  File /r "stage/*"
  WriteUninstaller "$INSTDIR\uninstall.exe"

  SetOutPath "$INSTDIR"
  CreateShortCut "$SMPROGRAMS\Same Page.lnk" "${PYW}" '"$INSTDIR\launcher.pyw"' "$INSTDIR\samepage.ico" 0 SW_SHOWNORMAL "" "Same Page 本地实时字幕与翻译"
  CreateShortCut "$DESKTOP\Same Page.lnk" "${PYW}" '"$INSTDIR\launcher.pyw"' "$INSTDIR\samepage.ico" 0 SW_SHOWNORMAL "" "Same Page 本地实时字幕与翻译"

  WriteRegStr HKCU "${UNINSTKEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "Publisher" "Same Page"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\samepage.ico"
  WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${UNINSTKEY}" "EstimatedSize" "$0"
SectionEnd

Section "Uninstall"
  !insertmacro StopRunning
  Delete "$SMPROGRAMS\Same Page.lnk"
  Delete "$DESKTOP\Same Page.lnk"
  RMDir /r "$INSTDIR\engine"
  Delete "$INSTDIR\launcher.pyw"
  Delete "$INSTDIR\samepage.ico"
  Delete "$INSTDIR\第三方许可.txt"
  Delete "$INSTDIR\LICENSE-WhisperLiveKit.txt"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTKEY}"
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "是否同时删除你的记录、录音和设置？$\r$\n（位置：$LOCALAPPDATA\Same Page）$\r$\n$\r$\n选“否”会保留它们，重新安装后还能继续使用。" IDNO done
    RMDir /r "$LOCALAPPDATA\Same Page"
  done:
SectionEnd
