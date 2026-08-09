; All custom installer text must be present in both supported languages.
; The hook is included before MUI_LANGUAGE defines symbolic language constants,
; so bind these strings to the stable Windows LCIDs directly (zh-CN 2052, en-US 1033).
Var AgentFriendDiagnosticPath

LangString AgentFriendAppRunning 2052 "agent-friend 的产品组件仍在运行，安装程序无法安全继续。请稍后重试；无需手动查找隐藏进程。"
LangString AgentFriendAppRunning 1033 "An agent-friend component is still running, so setup cannot continue safely. Please retry; you do not need to find hidden processes manually."
LangString AgentFriendPreinstallFailed 2052 "安装前检查未通过，agent-friend 尚未写入或替换程序文件。诊断信息已保存至：$\r$\n$AgentFriendDiagnosticPath$\r$\n$\r$\n请将该文件提供给开发者。"
LangString AgentFriendPreinstallFailed 1033 "The pre-install check failed before agent-friend program files were written or replaced. Diagnostics were saved to:$\r$\n$AgentFriendDiagnosticPath$\r$\n$\r$\nPlease provide that file to the developer."
LangString AgentFriendPreinstallFailedWithoutDiagnostics 2052 "安装前检查未通过，agent-friend 尚未写入或替换程序文件，而且安装器无法创建诊断文件。请将此截图提供给开发者。"
LangString AgentFriendPreinstallFailedWithoutDiagnostics 1033 "The pre-install check failed before agent-friend program files were written or replaced, and setup could not create a diagnostics file. Please provide a screenshot to the developer."
LangString AgentFriendIntegrityFailed 2052 "新版本完整性校验失败，未激活本次安装。安装程序将恢复原版本；请重新运行安装程序。"
LangString AgentFriendIntegrityFailed 1033 "The new version failed integrity verification and was not activated. Setup will restore the previous version; please run the installer again."
LangString AgentFriendTrustedWarning 2052 "这是包含受信凭据种子的敏感测试安装包。仅在确认来源可信时继续。种子会在校验后导入并立即清除。"
LangString AgentFriendTrustedWarning 1033 "This sensitive test installer contains a trusted credential seed. Continue only if you trust its source. The seed is imported after verification and then removed immediately."
LangString AgentFriendCredentialFailed 2052 "受信凭据导入失败，新版本未激活。安装程序将恢复原版本。"
LangString AgentFriendCredentialFailed 1033 "Trusted credential import failed, so the new version was not activated. Setup will restore the previous version."
LangString AgentFriendCleanupPending 2052 "新版本已安装成功；部分旧版文件暂时无法清理，将在以后升级时重试。"
LangString AgentFriendCleanupPending 1033 "The new version was installed successfully. Some inactive files could not be cleaned up and will be retried during a later upgrade."
LangString AgentFriendDowngradeBlocked 2052 "当前已安装的 agent-friend 是更新的版本，此安装包无法继续"
LangString AgentFriendDowngradeBlocked 1033 "A newer version of agent-friend is already installed. This installer cannot continue."
LangString AgentFriendRepairOrReinstall 2052 "修复或重新安装 agent-friend"
LangString AgentFriendRepairOrReinstall 1033 "Repair or reinstall agent-friend"
LangString AgentFriendMoveInstallRoot 2052 "agent-friend 已安装在：$\r$\n$0$\r$\n$\r$\n是否将程序文件迁移到：$\r$\n$1$\r$\n$\r$\n用户数据、设置、日志和凭据不会移动。"
LangString AgentFriendMoveInstallRoot 1033 "agent-friend is installed in:$\r$\n$0$\r$\n$\r$\nMove the program files to:$\r$\n$1$\r$\n$\r$\nUser data, settings, logs, and credentials will not be moved."
LangString AgentFriendInstallRecordBroken 2052 "检测到不完整的 agent-friend 安装记录。为避免产生两处安装，安装程序无法继续；请先修复或清理旧安装记录。"
LangString AgentFriendInstallRecordBroken 1033 "An incomplete agent-friend installation record was found. Setup cannot continue without risking two installations; repair or clear the old installation record first."

Var AgentFriendAutostartBeforeInstall
Var AgentFriendAutostartCaptured
Var AgentFriendTransactionStarted
Var AgentFriendRegisteredRoot
Var AgentFriendMoveIntent
Var AgentFriendInstallRootMove
Var AgentFriendDesktopShortcutExisted
Var AgentFriendInstallKind
Var AgentFriendHelperResult
Var AgentFriendHelperOutput
Var AgentFriendHelperSystemError
Var AgentFriendDiagnosticHandle
Var AgentFriendRuntimeExecutable

Function AgentFriendInitializeDiagnostics
  StrCpy $AgentFriendInstallKind "first-install"
  ${If} $AgentFriendRegisteredRoot != ""
    StrCpy $AgentFriendInstallKind "in-place"
  ${EndIf}
  ${If} $AgentFriendInstallRootMove == "1"
    StrCpy $AgentFriendInstallKind "explicit-move"
  ${EndIf}

  StrCpy $AgentFriendDiagnosticPath "$TEMP\${AGENT_FRIEND_PRODUCT_NAME}-installer-${AGENT_FRIEND_BUILD_ID}.log"
  ClearErrors
  FileOpen $AgentFriendDiagnosticHandle "$AgentFriendDiagnosticPath" w
  ${If} ${Errors}
    StrCpy $AgentFriendDiagnosticPath ""
    Return
  ${EndIf}
  FileWriteUTF16LE /BOM $AgentFriendDiagnosticHandle "schemaVersion=1$\r$\n"
  FileWriteUTF16LE $AgentFriendDiagnosticHandle "product=${PRODUCTNAME}$\r$\n"
  FileWriteUTF16LE $AgentFriendDiagnosticHandle "version=${VERSION}$\r$\n"
  FileWriteUTF16LE $AgentFriendDiagnosticHandle "buildId=${AGENT_FRIEND_BUILD_ID}$\r$\n"
  FileWriteUTF16LE $AgentFriendDiagnosticHandle "profileId=${AGENT_FRIEND_PROFILE_ID}$\r$\n"
  FileWriteUTF16LE $AgentFriendDiagnosticHandle "installKind=$AgentFriendInstallKind$\r$\n"
  FileWriteUTF16LE $AgentFriendDiagnosticHandle "installRoot=$INSTDIR$\r$\n"
  FileWriteUTF16LE $AgentFriendDiagnosticHandle "registeredRoot=$AgentFriendRegisteredRoot$\r$\n"
  FileWriteUTF16LE $AgentFriendDiagnosticHandle "installerPath=$EXEPATH$\r$\n"
  FileClose $AgentFriendDiagnosticHandle
FunctionEnd

!macro AGENT_FRIEND_RECORD_HELPER_RESULT phase
  ${If} $AgentFriendDiagnosticPath != ""
    ClearErrors
    FileOpen $AgentFriendDiagnosticHandle "$AgentFriendDiagnosticPath" a
    ${IfNot} ${Errors}
      FileSeek $AgentFriendDiagnosticHandle 0 END
      FileWriteUTF16LE $AgentFriendDiagnosticHandle "phase=${phase}$\r$\n"
      FileWriteUTF16LE $AgentFriendDiagnosticHandle "helperResult=$AgentFriendHelperResult$\r$\n"
      FileWriteUTF16LE $AgentFriendDiagnosticHandle "win32LastError=$AgentFriendHelperSystemError$\r$\n"
      FileWriteUTF16LE $AgentFriendDiagnosticHandle "helperOutput=$AgentFriendHelperOutput$\r$\n"
      FileClose $AgentFriendDiagnosticHandle
    ${EndIf}
  ${EndIf}
  DetailPrint "${PRODUCTNAME}: ${phase} helper result=$AgentFriendHelperResult; diagnostics=$AgentFriendDiagnosticPath"
!macroend

!macro AGENT_FRIEND_CAPTURE_HELPER_RESULT phase
  System::Call "kernel32::GetLastError() i .r0"
  Pop $AgentFriendHelperResult
  Pop $AgentFriendHelperOutput
  StrCpy $AgentFriendHelperSystemError "$0"
  !insertmacro AGENT_FRIEND_RECORD_HELPER_RESULT "${phase}"
!macroend

Function AgentFriendCaptureAutostart
  ${If} $AgentFriendAutostartCaptured == "1"
    Return
  ${EndIf}
  StrCpy $AgentFriendAutostartCaptured "1"
  ClearErrors
  ReadRegStr $AgentFriendAutostartBeforeInstall HKCU \
    "Software\Microsoft\Windows\CurrentVersion\Run" "${AGENT_FRIEND_AUTOSTART_VALUE}"
  ${If} ${Errors}
    StrCpy $AgentFriendAutostartBeforeInstall ""
  ${EndIf}
FunctionEnd

!macro AGENT_FRIEND_RESOLVE_INSTALL_ROOT_INTENT
  StrCpy $AgentFriendMoveIntent "0"
  StrCpy $AgentFriendInstallRootMove "0"
  ClearErrors
  ReadRegStr $AgentFriendRegisteredRoot SHCTX "${MANUPRODUCTKEY}" ""
  ${GetOptions} $CMDLINE "/MOVEINSTALL" $R9
  ${IfNot} ${Errors}
    StrCpy $AgentFriendMoveIntent "1"
  ${EndIf}
  ${If} $AgentFriendRegisteredRoot == ""
    Goto agent_friend_root_intent_done
  ${EndIf}
  GetFullPathName $0 "$AgentFriendRegisteredRoot"
  GetFullPathName $1 "$INSTDIR"
  ${If} $0 == $1
    StrCpy $AgentFriendMoveIntent "0"
    Goto agent_friend_root_intent_done
  ${EndIf}
  StrCpy $AgentFriendInstallRootMove "1"
  agent_friend_root_intent_done:
!macroend

!define MUI_CUSTOMFUNCTION_GUIINIT AgentFriendCaptureAutostart

Function AgentFriendRollback
  ${If} $AgentFriendTransactionStarted != "1"
    Return
  ${EndIf}
  ${IfNot} ${FileExists} "$PLUGINSDIR\${AGENT_FRIEND_INSTALLER_HELPER}"
    Return
  ${EndIf}
  nsExec::ExecToStack '"$PLUGINSDIR\${AGENT_FRIEND_INSTALLER_HELPER}" rollback --install-root "$INSTDIR" --transaction-id "${AGENT_FRIEND_BUILD_ID}"'
  !insertmacro AGENT_FRIEND_CAPTURE_HELPER_RESULT "rollback"
  StrCpy $AgentFriendTransactionStarted "0"
FunctionEnd

Function .onInstFailed
  Call AgentFriendRollback
FunctionEnd

Function AgentFriendUserAbort
  Call AgentFriendRollback
FunctionEnd

; Modern UI owns .onUserAbort and calls this documented extension point after
; its confirmation flow. Do not redefine the callback that MUI generates.
!define MUI_CUSTOMFUNCTION_ABORT AgentFriendUserAbort

!macro AGENT_FRIEND_FAIL message exitCode
  !define AgentFriendFailId ${__LINE__}
  Call AgentFriendRollback
  IfSilent agent_friend_fail_silent_${AgentFriendFailId} 0
  ${If} $PassiveMode != 1
    MessageBox MB_OK|MB_ICONSTOP "${message}"
  ${EndIf}
  agent_friend_fail_silent_${AgentFriendFailId}:
  DetailPrint "${PRODUCTNAME}: ${message}"
  SetErrorLevel ${exitCode}
  Quit
  !undef AgentFriendFailId
!macroend

; Tauri's default macro can force-kill by process name. Keep the final main-binary
; guard fail-closed; precise product-owned blocker handling is performed by the helper.
!macroundef CheckIfAppIsRunning
!macro CheckIfAppIsRunning executableName productName
  !if "${INSTALLMODE}" == "currentUser"
    nsis_tauri_utils::FindProcessCurrentUser "${executableName}"
  !else
    nsis_tauri_utils::FindProcess "${executableName}"
  !endif
  Pop $R0
  ${If} $R0 = 0
    !insertmacro AGENT_FRIEND_FAIL "$(AgentFriendAppRunning)" 32
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREINSTALL
  Call AgentFriendCaptureAutostart
  !insertmacro AGENT_FRIEND_RESOLVE_INSTALL_ROOT_INTENT
  ClearErrors
  ReadRegStr $R7 SHCTX "${UNINSTKEY}" "DisplayVersion"
  ${If} $R7 != ""
    nsis_tauri_utils::SemverCompare "${VERSION}" $R7
    Pop $R6
    ${If} $R6 = -1
      !insertmacro AGENT_FRIEND_FAIL "$(AgentFriendDowngradeBlocked)" 35
    ${EndIf}
  ${EndIf}
  ReadRegStr $R7 SHCTX "${UNINSTKEY}" "UninstallString"
  ${If} $R7 != ""
  ${AndIf} $AgentFriendRegisteredRoot == ""
    !insertmacro AGENT_FRIEND_FAIL "$(AgentFriendInstallRecordBroken)" 36
  ${EndIf}
  StrCpy $AgentFriendDesktopShortcutExisted "0"
  ${If} ${FileExists} "$DESKTOP\${PRODUCTNAME}.lnk"
    StrCpy $AgentFriendDesktopShortcutExisted "1"
  ${EndIf}
  ${If} ${FileExists} "$PLUGINSDIR\trusted-credential-seed.bin"
    IfSilent agent_friend_trusted_warning_done 0
    ${If} $PassiveMode != 1
      MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "$(AgentFriendTrustedWarning)" IDCANCEL agent_friend_trusted_cancel
    ${EndIf}
    Goto agent_friend_trusted_warning_done
    agent_friend_trusted_cancel:
      SetErrorLevel 33
      Quit
    agent_friend_trusted_warning_done:
  ${EndIf}

  Call AgentFriendInitializeDiagnostics

  ; The isolated smoke installer can deliberately remove only its extracted helper
  ; to prove that launch failures still leave useful pre-install diagnostics.
  !if "${AGENT_FRIEND_PROFILE_ID}" == "installer-smoke"
    ClearErrors
    ${GetOptions} $CMDLINE "/AGENTFRIENDSMOKEPREINSTALLLAUNCHFAIL" $R9
    ${IfNot} ${Errors}
      Delete "$PLUGINSDIR\${AGENT_FRIEND_INSTALLER_HELPER}"
    ${EndIf}
  !endif

  nsExec::ExecToStack '"$PLUGINSDIR\${AGENT_FRIEND_INSTALLER_HELPER}" preinstall --install-root "$INSTDIR" --registered-root "$AgentFriendRegisteredRoot" --move-intent "$AgentFriendMoveIntent" --target-existed-before-install "0" --transaction-id "${AGENT_FRIEND_BUILD_ID}" --main-binary-name "${MAINBINARYNAME}" --registry-product-name "${PRODUCTNAME}" --manufacturer "${MANUFACTURER}" --autostart-value-name "${AGENT_FRIEND_AUTOSTART_VALUE}" --start-menu-shortcut "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk" --legacy-start-menu-shortcut "$SMPROGRAMS\${PRODUCTNAME}.lnk" --desktop-shortcut "$DESKTOP\${PRODUCTNAME}.lnk"'
  !insertmacro AGENT_FRIEND_CAPTURE_HELPER_RESULT "preinstall"
  ${If} $AgentFriendHelperResult != 0
    ${If} $AgentFriendDiagnosticPath == ""
      !insertmacro AGENT_FRIEND_FAIL "$(AgentFriendPreinstallFailedWithoutDiagnostics)" 32
    ${Else}
      !insertmacro AGENT_FRIEND_FAIL "$(AgentFriendPreinstallFailed)" 32
    ${EndIf}
  ${EndIf}
  StrCpy $AgentFriendTransactionStarted "1"
!macroend

!macro NSIS_HOOK_POSTINSTALL
  StrCpy $AgentFriendRuntimeExecutable "$INSTDIR\${AGENT_FRIEND_PAYLOAD_RELATIVE}\runtime\python\agent-friend-runtime.exe"
  nsExec::ExecToStack '"$PLUGINSDIR\${AGENT_FRIEND_INSTALLER_HELPER}" verify --install-root "$INSTDIR" --payload-relative-path "${AGENT_FRIEND_PAYLOAD_RELATIVE}" --profile-id "${AGENT_FRIEND_PROFILE_ID}" --build-id "${AGENT_FRIEND_BUILD_ID}"'
  !insertmacro AGENT_FRIEND_CAPTURE_HELPER_RESULT "verify"
  ${If} $AgentFriendHelperResult != 0
    !insertmacro AGENT_FRIEND_FAIL "$(AgentFriendIntegrityFailed)" 34
  ${EndIf}

  ${If} ${FileExists} "$PLUGINSDIR\trusted-credential-seed.bin"
    ${IfNot} ${FileExists} "$PLUGINSDIR\trusted-credential-seed.key"
      Delete "$PLUGINSDIR\trusted-credential-seed.bin"
      !insertmacro AGENT_FRIEND_FAIL "$(AgentFriendCredentialFailed)" 33
    ${EndIf}
    ExecWait '"$AgentFriendRuntimeExecutable" seed-credentials --payload "$PLUGINSDIR\trusted-credential-seed.bin" --key-file "$PLUGINSDIR\trusted-credential-seed.key"' $R0
    Delete "$PLUGINSDIR\trusted-credential-seed.bin"
    Delete "$PLUGINSDIR\trusted-credential-seed.key"
    ${If} $R0 != 0
      !insertmacro AGENT_FRIEND_FAIL "$(AgentFriendCredentialFailed)" 33
    ${EndIf}
  ${ElseIf} ${FileExists} "$PLUGINSDIR\trusted-credential-seed.key"
    Delete "$PLUGINSDIR\trusted-credential-seed.key"
    !insertmacro AGENT_FRIEND_FAIL "$(AgentFriendCredentialFailed)" 33
  ${EndIf}

  nsExec::ExecToStack '"$PLUGINSDIR\${AGENT_FRIEND_INSTALLER_HELPER}" activate --install-root "$INSTDIR" --payload-relative-path "${AGENT_FRIEND_PAYLOAD_RELATIVE}" --profile-id "${AGENT_FRIEND_PROFILE_ID}" --build-id "${AGENT_FRIEND_BUILD_ID}" --transaction-id "${AGENT_FRIEND_BUILD_ID}"'
  !insertmacro AGENT_FRIEND_CAPTURE_HELPER_RESULT "activate"
  ${If} $AgentFriendHelperResult != 0
    !insertmacro AGENT_FRIEND_FAIL "$(AgentFriendIntegrityFailed)" 34
  ${EndIf}

  nsExec::ExecToStack '"$PLUGINSDIR\${AGENT_FRIEND_INSTALLER_HELPER}" cleanup --install-root "$INSTDIR" --profile-id "${AGENT_FRIEND_PROFILE_ID}" --build-id "${AGENT_FRIEND_BUILD_ID}"'
  !insertmacro AGENT_FRIEND_CAPTURE_HELPER_RESULT "cleanup"
  ${If} $AgentFriendHelperResult != 0
    DetailPrint "${PRODUCTNAME}: $(AgentFriendCleanupPending)"
  ${EndIf}

  ${If} $AgentFriendInstallRootMove == "1"
    nsExec::ExecToStack '"$PLUGINSDIR\${AGENT_FRIEND_INSTALLER_HELPER}" retire-source --install-root "$INSTDIR" --transaction-id "${AGENT_FRIEND_BUILD_ID}"'
    !insertmacro AGENT_FRIEND_CAPTURE_HELPER_RESULT "retire-source"
    ${If} $AgentFriendHelperResult != 0
      !insertmacro AGENT_FRIEND_FAIL "$(AgentFriendIntegrityFailed)" 34
    ${EndIf}
  ${EndIf}

  ${If} $AgentFriendAutostartBeforeInstall != ""
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
      "${AGENT_FRIEND_AUTOSTART_VALUE}" "$\"$INSTDIR\${MAINBINARYNAME}.exe$\" --autostart"
    DetailPrint "${PRODUCTNAME}: preserved Windows login startup."
  ${EndIf}

  ${If} $AgentFriendInstallRootMove == "1"
  ${AndIf} $AgentFriendDesktopShortcutExisted == "1"
    Delete "$DESKTOP\${PRODUCTNAME}.lnk"
    CreateShortcut "$DESKTOP\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
  ${EndIf}

  nsExec::ExecToStack '"$PLUGINSDIR\${AGENT_FRIEND_INSTALLER_HELPER}" commit --install-root "$INSTDIR" --profile-id "${AGENT_FRIEND_PROFILE_ID}" --build-id "${AGENT_FRIEND_BUILD_ID}" --transaction-id "${AGENT_FRIEND_BUILD_ID}"'
  !insertmacro AGENT_FRIEND_CAPTURE_HELPER_RESULT "commit"
  ${If} $AgentFriendHelperResult != 0
    !insertmacro AGENT_FRIEND_FAIL "$(AgentFriendIntegrityFailed)" 34
  ${EndIf}
  StrCpy $AgentFriendTransactionStarted "0"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !if "${INSTALLMODE}" == "currentUser"
    nsis_tauri_utils::FindProcessCurrentUser "${MAINBINARYNAME}.exe"
  !else
    nsis_tauri_utils::FindProcess "${MAINBINARYNAME}.exe"
  !endif
  Pop $R0
  ${If} $R0 = 0
    IfSilent agent_friend_uninstall_running_silent 0
    ${If} $PassiveMode != 1
      MessageBox MB_OK|MB_ICONSTOP "$(AgentFriendAppRunning)"
    ${EndIf}
    agent_friend_uninstall_running_silent:
    DetailPrint "${PRODUCTNAME}: $(AgentFriendAppRunning)"
    SetErrorLevel 32
    Quit
  ${EndIf}
!macroend
