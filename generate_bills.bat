@echo off
chcp 65001 > nul
:: Шлях до файлу логів (змініть на свій)
set LOGFILE=C:\Users\Оля\PythonProjects\HomeUtility\generate_bills.log

echo ======================================== >> %LOGFILE%
echo [START] %date% %time% >> %LOGFILE%
echo ======================================== >> %LOGFILE%

:: Зміна поточної робочої директорії на папку, де лежить цей батник
cd /d "%~dp0"

:: 2. Активуємо віртуальне оточення (якщо воно у вас є)
call ".venv\Scripts\activate.bat"

:: 3. Запускаємо вашу Django-команду
python manage.py generate_invoices >> %LOGFILE% 2>&1

:: 4. Деактивуємо оточення
call deactivate

echo ======================================== >> %LOGFILE%
echo [END] %date% %time% >> %LOGFILE%
echo ======================================== >> %LOGFILE%
