@echo off
:: Зміна поточної робочої директорії на папку, де лежить цей батник
cd /d "%~dp0"

:: Тепер можна спокійно запускати venv та скрипт через відносні шляхи
call .venv\Scripts\activate
python manage.py runserver 0.0.0.0:8000
