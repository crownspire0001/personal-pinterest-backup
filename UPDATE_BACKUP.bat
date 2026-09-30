@echo off
setlocal

cd /d "F:\Pinterest Backup"

title Pinterest Backup - Update

echo.
echo ============================================================
echo                 PINTEREST BACKUP UPDATE
echo ============================================================
echo.

echo [1/8] Updating Pinterest metadata...
echo.

py "backup_all.py"
if errorlevel 1 (
    echo.
    echo [ERROR] Metadata backup failed.
    pause
    exit /b 1
)

echo.
echo [OK] Metadata stage completed.
echo.

echo [2/8] Downloading new/missing media...
echo.

py "download_media.py"
if errorlevel 1 (
    echo.
    echo [ERROR] Media downloader failed.
    pause
    exit /b 1
)

echo.
echo [OK] Media download stage completed.
echo.

echo [3/8] Retrying failed media...
echo.

py "retry_failed.py"

echo.
echo [OK] Retry stage completed.
echo.

echo [4/8] Reconciling media records...
echo.

py "reconcile_media.py"
if errorlevel 1 (
    echo.
    echo [ERROR] Media reconciliation failed.
    pause
    exit /b 1
)

echo.
echo [OK] Media reconciliation completed.
echo.

echo [5/8] Reconciling media state...
echo.

py "reconcile_media_state.py"
if errorlevel 1 (
    echo.
    echo [ERROR] Media state reconciliation failed.
    pause
    exit /b 1
)

echo.
echo [OK] Media state reconciliation completed.
echo.

echo [6/8] Rebuilding offline gallery...
echo.

py "build_gallery_v2.py"
if errorlevel 1 (
    echo.
    echo [ERROR] Gallery rebuild failed.
    pause
    exit /b 1
)

echo.
echo [OK] Gallery rebuilt.
echo.

echo [7/8] Verifying backup...
echo.

call "VERIFY_BACKUP.bat"

echo.
echo [8/8] Recording backup history...
echo.

py "backup_history.py"

echo.
echo ============================================================
echo              PINTEREST UPDATE FINISHED
echo ============================================================
echo.

echo Gallery:
echo F:\Pinterest Backup\gallery\index.html
echo.
echo History:
echo F:\Pinterest Backup\backup\backup_history.json
echo.

pause
endlocal