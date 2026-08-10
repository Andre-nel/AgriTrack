# Codex Reference

## Pytest Command

Use this command when the normal Python launcher or virtualenv entry point is not behaving:

```powershell
$env:PYTHONPATH='C:\Farming\AgriTrack\venv\Lib\site-packages'; & 'C:\Users\Andre Nel\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests\unit\test_gate_service.py tests\integration\test_app_smoke.py -q
```
