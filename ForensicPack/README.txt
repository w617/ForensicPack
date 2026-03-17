ForensicPack Distribution Package
================================

Package: ForensicPack_Distribution
Contents
--------
- exe/ForensicPack/ForensicPack.exe
- exe/ForensicPack/_internal/
- python_source/ (includes updated README.md and FEATURES_GUIDE.md with all updates from 2026-03-13)
- EXE_SHA256.txt
- HASHES.txt

Run (EXE)
---------
1) Open exe/ForensicPack/
2) Run ForensicPack.exe

Run (Python source)
-------------------
Requirements:
- Python 3.10+
- 7-Zip installed if using 7z archives

From python_source/:
- GUI: python forensicpack.py
- CLI example: python forensicpack.py pack --source .\Input --output .\Output --format zip

Integrity
---------
Expected EXE SHA-256:
AC353B599519CC0BCBF79E378173F4572103E4EB17E8BB738BF62D8139EA66115

PowerShell verification command:
Get-FileHash -Algorithm SHA256 .\exe\ForensicPack\ForensicPack.exe
