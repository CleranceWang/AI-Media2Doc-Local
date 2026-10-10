@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "CFG=%ROOT%\config"
set "ENVEXAM=%CFG%\.env.example"
set "PYTHONIOENCODING=utf-8"

rem ---- 找凭据文件 ----
rem 为什么允许放在仓库外：.gitignore 是防线，不是保证。让真实凭据从未
rem 出现在仓库树里，比「写进去再靠规则挡住」更可靠。
rem 优先级：M2D_ENV 环境变量 > 仓库内 .env.local > 逐级向上的 .secrets\media2doc.env > config\.env
rem 逐级上找是为了不写死层数：仓库放在哪一层都能找到。
set "ENVFILE="
set "ENVSRC="

rem (1) 环境变量强制指定，最高优先级
if defined M2D_ENV if exist "%M2D_ENV%" ( set "ENVFILE=%M2D_ENV%" & set "ENVSRC=M2D_ENV" )

rem (2) 仓库内的 .env.local（已在 .gitignore）
if not defined ENVFILE if exist "%ROOT%\.env.local" (
  set "ENVFILE=%ROOT%\.env.local" & set "ENVSRC=.env.local"
)

rem (3) 从仓库根开始，最多向上 4 层找 .secrets\media2doc.env
if not defined ENVFILE (
  set "P=%ROOT%"
  for /l %%i in (1,1,4) do (
    if not defined ENVFILE (
      if exist "!P!\.secrets\media2doc.env" (
        set "ENVFILE=!P!\.secrets\media2doc.env"
        set "ENVSRC=!P!\.secrets\media2doc.env"
      )
      for %%p in ("!P!\..") do set "P=%%~fp"
    )
  )
)

rem (4) 兜底：仓库内 config\.env
if not defined ENVFILE if exist "%CFG%\.env" (
  set "ENVFILE=%CFG%\.env" & set "ENVSRC=config\.env"
)

if "%~1"=="" goto MENU
set "CMD=%~1"
goto DISPATCH

:MENU
cls
echo.
echo   ==========================================================
echo                     media2doc-local
echo   ==========================================================
echo.
echo   [1] 启动全部服务            up
echo   [2] 停止全部服务            down
echo   [3] 重建镜像并重启          rebuild
echo   [4] 实时日志                logs
echo   [5] 状态                    status
echo   [6] 首次部署向导            setup
echo   [7] 填 API Key              key
echo   [8] 健康检查                health
echo   [9] 检查依赖                doctor
echo   [A] 清空桶数据              clean
echo   [E] 看凭据来源              envinfo
echo   [0] 退出
echo.
set /p "SEL=  选择: "
echo.
if "!SEL!"=="1" set "CMD=up"
if "!SEL!"=="2" set "CMD=down"
if "!SEL!"=="3" set "CMD=rebuild"
if "!SEL!"=="4" set "CMD=logs"
if "!SEL!"=="5" set "CMD=status"
if "!SEL!"=="6" set "CMD=setup"
if "!SEL!"=="7" set "CMD=key"
if "!SEL!"=="8" set "CMD=health"
if "!SEL!"=="9" set "CMD=patch"
if /i "!SEL!"=="A" set "CMD=clean"
if "!SEL!"=="0" exit /b 0
if not defined CMD goto MENU
goto DISPATCH

:DISPATCH
if /i "%CMD%"=="up"      goto DO_UP
if /i "%CMD%"=="down"    goto DO_DOWN
if /i "%CMD%"=="restart" goto DO_RESTART
if /i "%CMD%"=="build"   goto DO_BUILD
if /i "%CMD%"=="rebuild" goto DO_REBUILD
if /i "%CMD%"=="logs"    goto DO_LOGS
if /i "%CMD%"=="status"  goto DO_STATUS
if /i "%CMD%"=="setup"   goto DO_SETUP
if /i "%CMD%"=="key"     goto DO_KEY
if /i "%CMD%"=="init"    goto DO_INIT
if /i "%CMD%"=="patch"   goto DO_DOCTOR
if /i "%CMD%"=="health"  goto DO_HEALTH
if /i "%CMD%"=="clean"   goto DO_CLEAN
if /i "%CMD%"=="envinfo" goto DO_ENVINFO
echo   未知子命令: %CMD%
goto END

:DO_UP
if not exist "%ENVFILE%" goto NOENV
echo   [启动] 全部服务...
docker compose --project-directory "%CFG%" --env-file "%ENVFILE%" up -d
echo.
echo   [完成] http://localhost:5173
goto END

:DO_DOWN
echo   [停止]...
docker compose --project-directory "%CFG%" --env-file "%ENVFILE%" down
goto END

:DO_RESTART
if not exist "%ENVFILE%" goto NOENV
echo   [重启]...
docker compose --project-directory "%CFG%" --env-file "%ENVFILE%" restart
goto END

:DO_BUILD
if not exist "%ENVFILE%" goto NOENV
echo   [构建]...
docker compose --project-directory "%CFG%" --env-file "%ENVFILE%" build
goto END

:DO_REBUILD
if not exist "%ENVFILE%" goto NOENV
echo   [重建] 镜像 然后 重启
echo.
rem 不再需要打补丁：改动已直接写入源码树
if errorlevel 1 goto END
echo   -- 重建镜像 --
docker compose --project-directory "%CFG%" --env-file "%ENVFILE%" build backend asr frontend
echo   -- 重启 --
docker compose --project-directory "%CFG%" --env-file "%ENVFILE%" up -d
echo.
echo   [完成] http://localhost:5173
goto END

:DO_LOGS
echo   [日志] Ctrl+C 退出
docker compose --project-directory "%CFG%" --env-file "%ENVFILE%" logs -f --tail 50
goto END

:DO_STATUS
echo   [状态]
docker compose --project-directory "%CFG%" --env-file "%ENVFILE%" ps -a
echo.
echo   -- 镜像 --
docker images --filter "reference=config-*" --format "  {{.Repository}}:{{.Tag}}  {{.CreatedSince}}  {{.Size}}"
goto END

:DO_SETUP
echo.
if exist "%ENVFILE%" goto SETUP_KEY
if not exist "%ENVEXAM%" goto NOENV
copy /y "%ENVEXAM%" "%ENVFILE%" >nul
echo   [1/3] 已生成 .env
:SETUP_KEY
call :ASK_KEY
echo   [3/3] 启动并初始化...
docker compose --project-directory "%CFG%" --env-file "%ENVFILE%" up -d --build
timeout /t 15 /nobreak >nul
pushd "%ROOT%"
python "scripts\init_rustfs.py"
popd
echo.
echo   [完成] http://localhost:5173
goto END

:DO_KEY
if not exist "%ENVFILE%" goto NOENV
call :ASK_KEY
goto END

:ASK_KEY
echo.
echo   ---- 填 API Key：只有 LLM_API_KEY 必填，其余回车跳过 ----
echo.
set "K1="
set /p "K1=  LLM_API_KEY (DeepSeek sk-...): "
if "!K1!"=="" goto ASK_K2
pushd "%ROOT%"
python "scripts\set_env.py" LLM_API_KEY "!K1!"
popd
:ASK_K2
set "K2="
set /p "K2=  RUSTFS_ACCESS_KEY (回车=不改): "
if "!K2!"=="" goto ASK_K3
pushd "%ROOT%"
python "scripts\set_env.py" RUSTFS_ACCESS_KEY "!K2!"
popd
:ASK_K3
set "K3="
set /p "K3=  RUSTFS_SECRET_KEY (回车=不改): "
if "!K3!"=="" goto ASK_DONE
pushd "%ROOT%"
python "scripts\set_env.py" RUSTFS_SECRET_KEY "!K3!"
popd
:ASK_DONE
echo.
pushd "%ROOT%"
python "scripts\check_llm.py"
popd
echo.
exit /b 0

:DO_INIT
if not exist "%ENVFILE%" goto NOENV
pushd "%ROOT%"
python "scripts\init_rustfs.py"
popd
goto END

:DO_DOCTOR
call :RUN_DOCTOR
goto END

:RUN_DOCTOR
echo   [检查] 依赖与配置...
pushd "%ROOT%"
python "scripts\doctor.py"
set "RC=%errorlevel%"
popd
if not "%RC%"=="0" echo   [注意] 有检查项未通过，见上方输出
exit /b %RC%

:DO_HEALTH
echo   [健康检查]
call :PING "前端" "http://127.0.0.1:5173"
call :PING "后端" "http://127.0.0.1:8080/docs"
call :PING "ASR " "http://127.0.0.1:8001/health"
call :PING_S3
goto END

:PING
set "NM=%~1"
set "UR=%~2"
curl -s -o nul -w "  %NM% HTTP %%{http_code}\n" --max-time 8 "%UR%" 2>nul
exit /b 0

:PING_S3
curl -s -o nul -w "  S3   HTTP %%{http_code}   (403 属正常: 无凭据访问本来就该被拒)\n" --max-time 8 "http://127.0.0.1:9000" 2>nul
exit /b 0

:DO_CLEAN
echo.
echo   !! 清空桶内所有音频与转写产物，任务记录不受影响。
set "OK="
set /p "OK=  输入 YES 确认: "
if /i not "%OK%"=="YES" goto END
docker exec m2d-rustfs sh -c "rm -rf /data/media2doc/transcripts /data/media2doc/*.mp3; ls -la /data/media2doc/"
echo   [完成] 桶已清空
goto END

:DO_ENVINFO
echo   [凭据来源]
if not defined ENVFILE (
  echo     ✗ 没找到任何 .env
  echo       跑 setup 生成 config\.env，或把凭据放到 ^<仓库上层^>\.secrets\media2doc.env
  goto END
)
echo     文件: %ENVFILE%
echo     来源: %ENVSRC%
pushd "%ROOT%"
python "scripts\env_info.py" "%ENVFILE%"
popd
goto END

:NOENV
:NOENV
echo.
echo   [错误] 找不到 %ENVFILE%
echo   先跑:  m2d.bat setup
echo.
goto END

:END
echo.
if "%~1"=="" pause
if "%~1"=="" goto MENU
exit /b 0
