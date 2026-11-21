Name "Test App"
OutFile "TestApp-2.1.0-x64-setup.exe"
InstallDir "$PROGRAMFILES64\TestApp"
RequestExecutionLevel admin
VIProductVersion "2.1.0.0"
VIAddVersionKey "ProductName" "Test App"
VIAddVersionKey "FileDescription" "Test App Setup"
VIAddVersionKey "FileVersion" "2.1.0"
Section
  SetOutPath "$INSTDIR"
  File "hello.exe"
  CreateDirectory "$SMPROGRAMS\Test App"
  CreateShortcut "$SMPROGRAMS\Test App\Test App.lnk" "$INSTDIR\hello.exe"
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd
Section "Uninstall"
  Delete "$INSTDIR\hello.exe"
SectionEnd
