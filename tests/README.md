# Pytest commands

## PowerShell

```powershell
cd "Your path to the scripts folder"
py -m pip install -r requirements.txt
py -m pytest
```

## Common pytest commands

Run all tests:

```bat
python -m pytest
```

Run one test file:

```bat
python -m pytest tests\test_utils.py
```

See the covereage of tests:

```bat
python -m pytest --cov=.
```
