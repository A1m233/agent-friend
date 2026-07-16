!macro REQUIRE_APP_CLOSED executableName operationName
  !define AppClosedCheckId ${__LINE__}

  retry_app_closed_${AppClosedCheckId}:
  !if "${INSTALLMODE}" == "currentUser"
    nsis_tauri_utils::FindProcessCurrentUser "${executableName}"
  !else
    nsis_tauri_utils::FindProcess "${executableName}"
  !endif
  Pop $R0
  ${If} $R0 = 0
    IfSilent app_closed_noninteractive_${AppClosedCheckId} 0
    ${If} $PassiveMode = 1
      Goto app_closed_noninteractive_${AppClosedCheckId}
    ${EndIf}
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION \
      "${PRODUCTNAME} is still running. Exit it from the system tray before ${operationName}, then click Retry." \
      IDRETRY retry_app_closed_${AppClosedCheckId} IDCANCEL app_closed_cancel_${AppClosedCheckId}

    app_closed_noninteractive_${AppClosedCheckId}:
      DetailPrint "${PRODUCTNAME}: ${executableName} is still running; refusing to ${operationName}."
      SetErrorLevel 32
      Quit

    app_closed_cancel_${AppClosedCheckId}:
      SetErrorLevel 32
      Quit
  ${EndIf}

  !undef AppClosedCheckId
!macroend

; Tauri's default macro force-kills a running process, especially for /S and
; passive installs. Replace it so the template's own install/uninstall check
; follows the same fail-closed policy as our pre-hooks.
!macroundef CheckIfAppIsRunning
!macro CheckIfAppIsRunning executableName productName
  !insertmacro REQUIRE_APP_CLOSED "${executableName}" "continue"
!macroend

!macro NSIS_HOOK_PREINSTALL
  ; Check the new binary name first.
  !insertmacro REQUIRE_APP_CLOSED "${MAINBINARYNAME}.exe" "install or update"

  ; During a binary rename, the running old version has a different process name.
  ; Tauri persists that name in the uninstall registry key, so fail closed for it too.
  ReadRegStr $R4 SHCTX "${UNINSTKEY}" "MainBinaryName"
  ${If} $R4 != ""
  ${AndIf} $R4 != "${MAINBINARYNAME}.exe"
    !insertmacro REQUIRE_APP_CLOSED "$R4" "install or update"
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro REQUIRE_APP_CLOSED "${MAINBINARYNAME}.exe" "uninstall"

  ; tauri-plugin-autostart uses the fixed app name as the Windows Run value.
  ; The package is current-user, so cleanup stays scoped to HKCU.
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "agent-friend"
!macroend
