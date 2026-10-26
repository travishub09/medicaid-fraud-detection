@echo off
REM merge_push.bat N  --  merge bundleN from Downloads into the dev branch, push, close the task.
REM
REM   merge_push.bat 319
REM
REM ONE command to paste, so no second pasted line exists for a child process to swallow
REM (the "'t' is not recognized" failure that ate the git push line after merge_bundle.bat,
REM 2026-10-13). Prints the head at the end; the reminder task merge_bundleN is closed only
REM when the push succeeded.
setlocal
set N=%~1
if "%N%"=="" (
  echo usage: merge_push.bat 319
  exit /b 1
)
set REPO=C:\Users\treyr\medicaid-fraud-detection
set BRANCH=claude/happy-mccarthy-3skrn3
set BUNDLE=C:\Users\treyr\bundle%N%.bundle
cd /d "%REPO%"
for %%f in ("%USERPROFILE%\Downloads\bundle%N%*.bundle") do copy /y "%%f" "%BUNDLE%" >nul
if not exist "%BUNDLE%" (
  echo bundle%N%.bundle is not in Downloads: download it from the chat first
  exit /b 1
)
git merge --abort >nul 2>&1
REM the bundle carries the reminder state; local --done-task edits are re-applied by the
REM close-out lines after the merge (a dirty reminders.json made the ff-merge abort, bundle 320).
REM No inline <nul redirections: cmd mangled "<nul ||" into a command named 'ul' (bundle 321).
git checkout -- docs/reminders.json docs/OPERATOR_REMINDERS.md >nul 2>&1
git fetch "%BUNDLE%" %BRANCH%
if errorlevel 1 (
  echo fetch from the bundle failed: paste this window to Claude
  exit /b 1
)
git merge --ff-only FETCH_HEAD
if errorlevel 1 (
  echo merge failed, not a fast-forward: paste this window to Claude
  exit /b 1
)
git push origin %BRANCH%
if errorlevel 1 (
  echo push failed: paste this window to Claude
  exit /b 1
)
REM freeze tags (2026-10-14): a bundle may carry refs/tags/freeze-* (a code snapshot to
REM return to). Fetching them is harmless when the bundle has none; the push is best-effort.
git fetch "%BUNDLE%" "refs/tags/freeze-*:refs/tags/freeze-*" >nul 2>&1
git push origin "refs/tags/freeze-*:refs/tags/freeze-*" >nul 2>&1
REM repo migration overlap (2026-10-26): while the repo moves to the operator's own GitHub,
REM every remote besides origin (the old one, renamed 'travis') gets the same push, best-effort,
REM so both copies stay in step. With one remote this loop does nothing.
for /f "usebackq" %%r in (`git remote`) do if /i not "%%r"=="origin" (
  git push %%r %BRANCH% >nul 2>&1 && echo mirrored to remote %%r || echo mirror push to remote %%r failed (origin is pushed; not fatal)
  git push %%r "refs/tags/freeze-*:refs/tags/freeze-*" >nul 2>&1
)
python -m src.reminders --done-task merge_bundle%N%
git log --oneline -1
echo DONE: bundle %N% merged and pushed. Next: the VM pull line.
endlocal
