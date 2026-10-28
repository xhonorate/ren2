@echo off
setlocal enabledelayedexpansion

rem ===========================================================================
rem  ComfyUI, headless, with the flags this project needs.
rem
rem  The one that matters is --disable-cuda-malloc.
rem
rem  ComfyUI turns on PyTorch's cudaMallocAsync allocator by default ("enabled
rem  by default for torch 2.0 and up", per its own --help). Under that
rem  allocator, freeing a model does not hand the VRAM back to the OS -- the
rem  pool stays reserved by the process. Measured on this machine: after
rem  POST /free with unload_models and free_memory both set, ComfyUI still
rem  reported torch_vram_total 8.06 GB with 0.21 GB free inside it, and
rem  nvidia-smi showed 9423 MiB used before and 9423 MiB after.
rem
rem  That is why the editor's VRAM swap reports success and frees nothing, and
rem  why loading a 14.3 GB LLM fails on a 16 GB card while ComfyUI is up:
rem  "llama-server exited before becoming healthy". With the allocator
rem  disabled, /free actually returns the memory and the swap works.
rem
rem  Cost: the async allocator is a little faster at churning buffers. On one
rem  card that has to be shared with an LLM, being able to give the memory
rem  back is worth more than that.
rem
rem  Do not run this while ComfyUI Desktop is running -- they both want 8188.
rem  Quit the desktop app first (tray icon included).
rem
rem  ---------------------------------------------------------------------
rem  IF THIS DIES WITH "OPENSSL_Uplink(...): no OPENSSL_Applink"
rem
rem  That is not these flags. The same crash happens running ComfyUI's own
rem  command line with none of them, and it survives a minimal PATH and
rem  --disable-all-custom-nodes -- so on a machine where it appears, ComfyUI
rem  cannot be started from a plain shell at all. Something in the Desktop
rem  app's environment is load-bearing.
rem
rem  Use the Desktop app's own launch arguments instead, which get the same
rem  result. Either its settings UI, or this file:
rem
rem    %APPDATA%\Comfy Desktop\installations.json
rem
rem  find  "launchArgs": "--enable-manager"
rem  make  "launchArgs": "--enable-manager --disable-cuda-malloc --reserve-vram 1.5"
rem
rem  Quit Comfy Desktop before editing, or it will write the file back on
rem  exit and undo the change.
rem  ---------------------------------------------------------------------
rem ===========================================================================

rem --- where ComfyUI lives ---------------------------------------------------
rem Taken from the command line ComfyUI Desktop itself uses, so this launches
rem the same install with the same models rather than a second one.
set "COMFY_ROOT=D:\Comfy-Desktop\ComfyUI-Installs\ComfyUI"
set "COMFY_PY=%COMFY_ROOT%\ComfyUI\.venv\Scripts\python.exe"
set "MODEL_PATHS=%APPDATA%\Comfy Desktop\instance-model-paths\inst-1788177677393.yaml"
set "INPUT_DIR=D:\Comfy-Desktop\ComfyUI-Shared\input"
set "OUTPUT_DIR=D:\Comfy-Desktop\ComfyUI-Shared\output"

rem --- what to leave for everything else -------------------------------------
rem VRAM in GB held back for the OS and the desktop apps. This machine runs a
rem 16 GB card with Discord, Godot and the rest drawing on it, and they were
rem holding over a gigabyte between them. Raise it if renders start fighting
rem the desktop; lower it if a big render will not fit.
rem Each of these can be overridden from the environment without editing the
rem file -- `set COMFY_PORT=8189 && start-comfyui.bat` to bring up a second
rem instance beside a running one, say.
if not defined RESERVE_VRAM set "RESERVE_VRAM=1.5"
if not defined COMFY_HOST set "COMFY_HOST=127.0.0.1"
if not defined COMFY_PORT set "COMFY_PORT=8188"

rem ===========================================================================

echo.
echo  ComfyUI (headless)  %COMFY_HOST%:%COMFY_PORT%
echo  ------------------------------------------------------------------
echo   cudaMallocAsync : OFF   ^(so /free actually returns VRAM^)
echo   reserved VRAM   : %RESERVE_VRAM% GB
echo.

if not exist "%COMFY_PY%" (
    echo  ERROR: no python at
    echo         %COMFY_PY%
    echo.
    echo  Set COMFY_ROOT at the top of this file to your ComfyUI install.
    goto :fail
)

if not exist "%COMFY_ROOT%\ComfyUI\main.py" (
    echo  ERROR: no main.py under %COMFY_ROOT%\ComfyUI
    goto :fail
)

rem Without this yaml ComfyUI starts fine and then cannot see a single
rem checkpoint or LoRA, which looks like the models are gone rather than like
rem a missing argument.
if not exist "%MODEL_PATHS%" (
    echo  WARNING: model paths config not found at
    echo           %MODEL_PATHS%
    echo           Checkpoints and LoRAs will be missing. Continuing anyway.
    echo.
    set "MODEL_PATHS="
)

rem A second instance does not fail cleanly -- it half-starts, loses the port,
rem and leaves you wondering why the editor cannot reach it.
netstat -ano | findstr /r /c:"LISTENING" | findstr /c:":%COMFY_PORT% " >nul 2>&1
if %errorlevel%==0 (
    echo  ERROR: something is already listening on port %COMFY_PORT%.
    echo         That is almost always ComfyUI Desktop -- quit it, including
    echo         the system tray icon, and run this again.
    goto :fail
)

pushd "%COMFY_ROOT%" || goto :fail

rem Put ComfyUI's own DLLs in front of everything else on PATH.
rem
rem Windows resolves a DLL by walking PATH, so any other copy of OpenSSL that
rem happens to sit earlier wins. On this machine miniconda3\Library\bin is on
rem the system PATH and carries its own libcrypto-3-x64.dll, which loads into
rem ComfyUI's Python and then dies with:
rem
rem     OPENSSL_Uplink(...): no OPENSSL_Applink
rem
rem -- an OpenSSL built against a different C runtime, refusing to do file I/O.
rem ComfyUI Desktop never hits this because it launches with an environment of
rem its own; a plain shell does hit it, which is why starting ComfyUI by hand
rem fails here and starting it from the app does not.
set "PATH=%COMFY_ROOT%\standalone-env;%COMFY_ROOT%\standalone-env\DLLs;%COMFY_ROOT%\standalone-env\Library\bin;%COMFY_ROOT%\ComfyUI\.venv\Scripts;%PATH%"

set "ARGS=-s ComfyUI\main.py"
set "ARGS=%ARGS% --disable-cuda-malloc"
set "ARGS=%ARGS% --disable-auto-launch"
set "ARGS=%ARGS% --listen %COMFY_HOST% --port %COMFY_PORT%"
set "ARGS=%ARGS% --reserve-vram %RESERVE_VRAM%"
set "ARGS=%ARGS% --enable-manager"
set "ARGS=%ARGS% --input-directory "%INPUT_DIR%""
set "ARGS=%ARGS% --output-directory "%OUTPUT_DIR%""
if defined MODEL_PATHS set "ARGS=%ARGS% --extra-model-paths-config "%MODEL_PATHS%""

echo  starting...
echo.
"%COMFY_PY%" %ARGS% %*

set "RC=%errorlevel%"
popd

if not "%RC%"=="0" (
    echo.
    echo  ComfyUI exited with code %RC%.
    goto :fail
)
endlocal
exit /b 0

:fail
echo.
pause
endlocal
exit /b 1
