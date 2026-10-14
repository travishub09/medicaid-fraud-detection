@echo off
REM merge_push.bat N  --  merge bundleN from Downloads into the dev branch, push, close the task.
REM
REM   merge_push.bat 319
REM
REM ONE line to paste. Every child command reads from NUL so nothing it runs can swallow
REM the first letters of the next pasted line (the "'t' is not recognized" failure that ate
REM the git push line after merge_bundle.bat, 2026-10-13). Prints the head at the end; the
REM reminder task merge_bundleN is closed only when the push succeeded.
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
git merge --abort <nul >nul 2>&1
REM the bundle carries the reminder state; local --done-task edits are re-applied by the
REM close-out lines after the merge (a dirty reminders.json made the ff-merge abort, bundle 320)
git checkout -- docs/reminders.json docs/OPERATOR_REMINDERS.md <nul >nul 2>&1
git fetch "%BUNDLE%" %BRANCH% <nul || (echo fetch from the bundle failed: paste this window to Claude & exit /b 1)
git merge --ff-only FETCH_HEAD <nul || (echo merge failed ^(not a fast-forward^): paste this window to Claude & exit /b 1)
git push origin %BRANCH% <nul || (echo push failed: paste this window to Claude & exit /b 1)
python -m src.reminders --done-task merge_bundle%N% <nul
git log --oneline -1
echo DONE: bundle %N% merged and pushed. Next: the VM pull line.
endlocal
