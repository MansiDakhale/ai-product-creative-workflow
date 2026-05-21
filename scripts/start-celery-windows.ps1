# Celery on Windows MUST use solo pool (prefork/spawn causes PermissionError)
Set-Location "$PSScriptRoot\..\backend"
.\venv\Scripts\Activate.ps1
celery -A api.celery_app worker --loglevel=info --pool=solo -c 1
