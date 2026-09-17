@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ==========================================
echo  珠宝同款检索 - 安装（只需运行一次）
echo ==========================================
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo [失败] 没找到 Python 启动器 py。请先安装 Python 3.11（勾选 "Add to PATH"），再重新运行。
  pause & exit /b 1
)
py -3.11 --version >nul 2>nul
if errorlevel 1 (
  echo [失败] 没找到 Python 3.11。请从 python.org 安装 3.11 版本后重试。
  pause & exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/5] 创建虚拟环境 .venv ...
  py -3.11 -m venv .venv
  if errorlevel 1 ( echo [失败] 创建虚拟环境失败 & pause & exit /b 1 )
) else (
  echo [1/5] 虚拟环境已存在，跳过
)
set PY=.venv\Scripts\python.exe

echo [2/5] 升级 pip ...
"%PY%" -m pip install --upgrade pip -q
if errorlevel 1 echo [警告] pip 升级失败，继续尝试

echo [3/5] 安装基础依赖（transformers / pillow / numpy）...
"%PY%" -m pip install -r search_service\requirements.txt -q
if errorlevel 1 ( echo [失败] 基础依赖安装失败，请检查网络后重试 & pause & exit /b 1 )

echo [4/5] 安装 PyTorch ...
"%PY%" -c "import torch" >nul 2>nul
if not errorlevel 1 (
  echo    已有 torch，先检查能否用显卡
  "%PY%" -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() and 'sm_61' in torch.cuda.get_arch_list() else 1)" >nul 2>nul
  if not errorlevel 1 ( echo    显卡版 torch 已可用 & goto :models )
)
echo    第一步：先装 CPU 版保证能用
"%PY%" -m pip install "torch==2.7.1" --index-url https://download.pytorch.org/whl/cpu -q
if errorlevel 1 ( echo [失败] CPU 版 torch 安装失败，请检查网络后重试 & pause & exit /b 1 )
"%PY%" -c "import torch; print('   CPU 版 torch', torch.__version__, '可用')"

nvidia-smi >nul 2>nul
if errorlevel 1 (
  echo    没检测到 NVIDIA 显卡，使用 CPU 版（认图会慢很多，建议过夜）
  goto :models
)
echo    第二步：检测到 NVIDIA 显卡，尝试换装显卡版（P104-100 需要 cu126）...
"%PY%" -m pip install "torch==2.7.1" --index-url https://download.pytorch.org/whl/cu126 -q --force-reinstall --no-deps
"%PY%" -c "import torch,sys; ok=torch.cuda.is_available() and 'sm_61' in torch.cuda.get_arch_list(); print('   显卡版自检:', 'OK' if ok else '不可用'); sys.exit(0 if ok else 1)"
if not errorlevel 1 goto :models
echo    cu126 不可用，再试 cu118 ...
"%PY%" -m pip install "torch==2.7.1" --index-url https://download.pytorch.org/whl/cu118 -q --force-reinstall --no-deps
"%PY%" -c "import torch,sys; ok=torch.cuda.is_available() and 'sm_61' in torch.cuda.get_arch_list(); print('   显卡版自检:', 'OK' if ok else '不可用'); sys.exit(0 if ok else 1)"
if not errorlevel 1 goto :models
echo    显卡版都不可用，退回 CPU 版
"%PY%" -m pip install "torch==2.7.1" --index-url https://download.pytorch.org/whl/cpu -q --force-reinstall --no-deps

:models
echo [5/5] 下载模型（约 1 GB，第一次较慢；国内可在 .env 里加 HF_ENDPOINT=https://hf-mirror.com）...
"%PY%" -m search_service.prefetch
if errorlevel 1 (
  echo.
  echo [失败] 模型下载或自检失败。可选处理：
  echo   1. 在 .env 里加一行 HF_ENDPOINT=https://hf-mirror.com 后重新运行本脚本
  echo   2. 或把离线模型目录放到 models\facebook__dinov2-base 和 models\openai__clip-vit-base-patch32
  pause & exit /b 1
)

echo.
echo ==========================================
echo  安装完成。接下来双击 启动.bat
echo ==========================================
pause
