# Unattended test run of build\v131072\oot.exe: extra OOT_* settings, a start that crashes is retried, the outcome printed.
#   tools\dev\run_oot.ps1 -Seconds 40 -Name skip -Env @{OOT_SKIP_OPS='DISPATCH'} [-Tries 3] [-Limit 400] [-Extra @('--shot','x.png')]
# Logs: build\logs\<Name>.log (stderr), <Name>.out (stdout).
#
# OOT_ASSUME_FOCUS=1 and OOT_BACKGROUND_WINDOW=1 are always set. Without them the game window tells the game when it
# loses focus, the game stops presenting until focus returns, and a run looks frozen whenever the PC's owner clicks
# on another window (2026-11-06: stalls of 5 s to 8 minutes read as hangs). The window also stays behind the owner's.
param([int]$Seconds = 40, [string]$Name = 'run', [hashtable]$Env = @{}, [int]$Tries = 3, [int]$Limit = 400, [string[]]$Extra = @())
$d = Join-Path (Split-Path (Split-Path $PSScriptRoot)) 'build'
$base = @{ OOT_VOLUME = '0'; OOT_ASYNC_PREEMPT = '0'; OOT_TRACE = 'perf'; OOT_ASSUME_FOCUS = '1'; OOT_BACKGROUND_WINDOW = '1' }
foreach ($k in $Env.Keys) { $base[$k] = $Env[$k] }
foreach ($k in $base.Keys) { Set-Item -Path "Env:\$k" -Value $base[$k] }
New-Item -ItemType Directory -Force "$d\logs" | Out-Null   # a fresh clone has no build\logs yet
$log = "$d\logs\$Name.log"
for ($t = 1; $t -le $Tries; $t++) {
    $t0 = Get-Date
    $p = Start-Process -FilePath "$d\v131072\oot.exe" -ArgumentList (@('--seconds', "$Seconds") + $Extra) -WorkingDirectory "$d\v131072" `
        -RedirectStandardOutput "$d\logs\$Name.out" -RedirectStandardError $log -NoNewWindow -PassThru
    while (-not $p.HasExited -and ((Get-Date) - $t0).TotalSeconds -lt $Limit) { Start-Sleep -Seconds 3 }
    if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force; "try $t stopped after $Limit s"; break }
    $p.WaitForExit()
    $crash = Select-String -LiteralPath $log -Pattern 'FATAL|left to its own handler' -Quiet
    if ($crash) { Copy-Item -LiteralPath $log -Destination "$d\logs\$Name.crash$t.log" -Force }   # the next try overwrites the log
    "try $t exit $($p.ExitCode) after $([int]((Get-Date) - $t0).TotalSeconds) s$(if ($crash) { ' (crash)' })"
    if (-not $crash) { break }
}
foreach ($k in $base.Keys) { Remove-Item -Path "Env:\$k" -ErrorAction SilentlyContinue }
Select-String -LiteralPath $log -Pattern 'perf: frame \d+: (interval|gpu)|boot: frames' | Select-Object -Last 3 |
    ForEach-Object { $_.Line.Trim().Substring(0, [math]::Min(200, $_.Line.Trim().Length)) }
