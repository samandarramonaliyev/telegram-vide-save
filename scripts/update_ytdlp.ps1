# Upgrade yt-dlp in the local venv (Windows). Usage:  .\scripts\update_ytdlp.ps1
$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -m pip install --upgrade yt-dlp
& $py -c "import yt_dlp; print('yt-dlp', yt_dlp.version.__version__)"
Write-Host "Restart the bot to apply the new version."
