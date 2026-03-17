ForensicPack Distribution Package
================================

Package: ForensicPack_Distribution
Contents
--------
- release/windows/ForensicPack/ForensicPack.exe
- release/windows/ForensicPack/_internal/
- src/ (Python source)
- checksums/EXE_SHA256.txt
- checksums/HASHES.txt

Run (EXE)
---------
1) Open release/windows/ForensicPack/
2) Run ForensicPack.exe

Run (Python source)
-------------------
Requirements:
- Python 3.10+
- 7-Zip installed if using 7z archives

From src/:
- GUI: python forensicpack.py
- CLI example: python forensicpack.py pack --source .\Input --output .\Output --format zip

Integrity
---------
Expected EXE SHA-256:
AC353B599519CC0BCBF79E378173F4572103E4EB17E8BB738BF62D8139EA66115

PowerShell verification command:
Get-FileHash -Algorithm SHA256 .\release\windows\ForensicPack\ForensicPack.exe
