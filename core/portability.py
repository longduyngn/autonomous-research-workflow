"""Resolve installed environments without baking a host platform into config."""
from pathlib import Path
import os
import shutil


def environment_python(directory):
    root=Path(directory)
    candidates=[root/'Scripts/python.exe',root/'bin/python',root/'bin/python3']
    for path in candidates:
        if path.is_file():return path
    raise FileNotFoundError('No Python executable in environment: '+str(root))


def site_packages(directory):
    root=Path(directory)
    return [p for p in [root/'Lib/site-packages',*sorted((root/'lib').glob('python*/site-packages'))] if p.is_dir()]


def resolve_command(command, controller):
    parts=[part.replace('{controller}',str(controller)) for part in command]
    if parts and not Path(parts[0]).exists() and 'colab-runtime' in parts[0]:
        parts[0]=str(environment_python(Path(controller)/'storage/tools/colab-runtime'))
    return parts


def browser_executable(configured=''):
    value=os.environ.get('CHROME_PATH') or os.environ.get('COLAB_BROWSER') or configured
    if value:return value
    for name in ('chromium','chromium-browser','google-chrome','chrome','msedge'):
        found=shutil.which(name)
        if found:return found
    candidates=[Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'CocCoc/Browser/Application/browser.exe',
                Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe']
    return next((str(p) for p in candidates if p.is_file()),'')
