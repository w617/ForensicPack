# Windows Code Signing

ForensicPack intentionally does not store signing certificates, private keys, certificate passwords, or signing-service credentials in the repository.

The Windows build script accepts an approved external signing hook:

```powershell
.\src\scripts\build_windows.ps1 -SigningScript C:\SecureBuild\Sign-ForensicPack.ps1
```

The hook receives the absolute path to `ForensicPack.exe` as its first argument. It should:

1. Sign the executable with the agency or publisher certificate.
2. Use SHA-256 for the file digest.
3. Apply an RFC 3161 trusted timestamp.
4. Verify the completed signature.
5. Return a nonzero exit code if signing or verification fails.

Example hook structure:

```powershell
param([Parameter(Mandatory=$true)][string]$ExecutablePath)

$signTool = "C:\Program Files (x86)\Windows Kits\10\bin\x64\signtool.exe"
$certificatePath = $env:FORENSICPACK_SIGNING_CERT_PATH
$certificatePassword = $env:FORENSICPACK_SIGNING_CERT_PASSWORD

& $signTool sign /fd SHA256 /td SHA256 /tr "https://timestamp.digicert.com" `
    /f $certificatePath /p $certificatePassword $ExecutablePath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $signTool verify /pa /v $ExecutablePath
exit $LASTEXITCODE
```

Store certificate material in a protected secret store, hardware token, managed signing service, or secured CI runner. Do not add it to Git, build artifacts, logs, or issue attachments.
