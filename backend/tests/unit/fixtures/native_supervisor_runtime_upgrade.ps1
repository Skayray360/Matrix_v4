# Creado por Aldo Garcia.
# Inert archives and mocked tools only; no Windows process/service is started.
param([Parameter(Mandatory=$true)][string]$Controller)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Controller,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'AST parse failed' }
foreach ($function in $ast.FindAll({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] },$false)) { Invoke-Expression $function.Extent.Text }
$script:Root=Join-Path ([IO.Path]::GetTempPath()) ('matrix ! (&) upgrade ' + [guid]::NewGuid().ToString('N'))
$script:Runtime=Join-Path $script:Root 'runtime'
$script:Run=Join-Path $script:Root 'run'
$script:Utf8=[Text.UTF8Encoding]::new($false)
function Assert([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Reject([scriptblock]$Body,[string]$Pattern) {
    $message=''
    try { & $Body | Out-Null } catch { $message=$_.Exception.Message }
    Assert ($message -match $Pattern) ('Unexpected rejection: ' + $message)
}
function New-Pin([string]$Name,[string]$Version,[string]$Relative) {
    $source=Join-Path $script:Root ('source-' + [guid]::NewGuid().ToString('N'))
    Write-Text (Join-Path $source $Relative) ('INERT ' + $Name + ' ' + $Version)
    $archive=Join-Path $script:Runtime ('downloads/' + $Name + '-' + $Version + '.zip')
    Ensure-Directory (Split-Path -Parent $archive)
    [IO.Compression.ZipFile]::CreateFromDirectory($source,$archive)
    return [pscustomobject]@{version=$Version;url='https://nodejs.org/fixture.zip';sha256=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()}
}
try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    Ensure-Directory $script:Runtime
    Ensure-Directory $script:Run
    Assert ((Quote-Argument 'D:\Wamp ! (&)\www\Matrix RH\') -eq '"D:\Wamp ! (&)\www\Matrix RH\\"') 'Windows D: argument punctuation or trailing slash was corrupted'
    $old=New-Pin 'node' '1.0.0' 'v1/node.exe'
    $new=New-Pin 'node' '2.0.0' 'v2/node.exe'
    $oldExe=Join-Path $script:Runtime 'node/v1/node.exe'
    $newExe=Join-Path $script:Runtime 'node/v2/node.exe'
    Install-ZipRuntime 'node' $old $oldExe
    Reject { Install-ZipRuntime 'node' $new $newExe } 'Cambio de version no autorizado'
    Assert (Test-Path -LiteralPath $oldExe) 'Unauthorized upgrade changed the old runtime'
    $script:FailPublish=$true; $script:FailPythonBackup=$false
    function Move-Item {
        param($LiteralPath,$Destination,$ErrorAction)
        if ($script:FailPublish -and $LiteralPath -like '*.staging-*') { $script:FailPublish=$false; throw 'SYNTHETIC publication denied' }
        if ($script:FailPythonBackup -and $LiteralPath -eq (Join-Path $script:Runtime 'python')) { $script:FailPythonBackup=$false; throw 'SYNTHETIC Python in use' }
        Microsoft.PowerShell.Management\Move-Item -LiteralPath $LiteralPath -Destination $Destination -ErrorAction Stop
    }
    Reject { Install-ZipRuntime 'node' $new $newExe -AllowUpgrade } 'No se pudo publicar'
    Assert (Test-Path -LiteralPath $oldExe) 'Failed publication did not restore old Node'
    Assert (-not (Test-Path -LiteralPath $newExe)) 'Failed publication left new Node active'
    Assert (([IO.File]::ReadAllText((Join-Path $script:Runtime 'node/.matrix-package.json')) | ConvertFrom-Json).version -eq '1.0.0') 'Rollback restored the wrong receipt'
    Install-ZipRuntime 'node' $new $newExe -AllowUpgrade
    Assert (Test-Path -LiteralPath $newExe) 'Valid upgrade was not published'
    $backups=@(Get-ChildItem -LiteralPath $script:Runtime -Directory -Filter 'node.previous-*')
    Assert ($backups.Count -eq 1 -and (Test-Path -LiteralPath (Join-Path $backups[0].FullName 'v1/node.exe'))) 'Verified old runtime was not retained'
    Reject { Install-ZipRuntime 'node' $old $oldExe -AllowUpgrade } 'Cambio de version no autorizado'
    $incomplete=New-Pin 'node' '3.0.0' 'unexpected.exe'
    Reject { Install-ZipRuntime 'node' $incomplete (Join-Path $script:Runtime 'node/v3/node.exe') -AllowUpgrade } 'no contiene el ejecutable esperado'
    Assert ([IO.File]::ReadAllText($newExe) -eq 'INERT node 2.0.0') 'An incomplete candidate changed the active runtime'
    Write-Text $newExe 'TAMPERED'
    Reject { Install-ZipRuntime 'node' $new $newExe -AllowUpgrade } 'Integridad incorrecta'
    Reject { Install-ZipRuntime 'mysql' $new $newExe -AllowUpgrade } 'solo administra Node y uv'
    $uvOld=New-Pin 'uv' '0.12.23' 'uv.exe'
    $uvNew=New-Pin 'uv' '0.13.5' 'uv.exe'
    $uvExe=Join-Path $script:Runtime 'uv/uv.exe'
    Install-ZipRuntime 'uv' $uvOld $uvExe
    Install-ZipRuntime 'uv' $uvNew $uvExe -AllowUpgrade
    Assert ([IO.File]::ReadAllText($uvExe) -eq 'INERT uv 0.13.5') 'uv upgrade failed'

    # Python/venv must be replaced together, with canonical absolute paths.
    $pin=[pscustomobject]@{version='3.12.15';build='20261021'}
    $script:Python='Synthetic-Python'
    $script:PythonMode='ok'
    $script:Managed=Join-Path $script:Runtime 'python/cpython-3.12.15-windows-x86_64-none'
    $script:ProbeBase=$script:Managed
    $script:ProbeVersion='3.12.15'; $script:ProbeBits=64
    function Synthetic-Python {
        Assert ($args[0] -eq '-I' -and $args[1] -eq '-c') 'Python probe was not isolated'
        $script:LASTEXITCODE=0
        return (@{version=$script:ProbeVersion;bits=$script:ProbeBits;base=$script:ProbeBase} | ConvertTo-Json -Compress)
    }
    function Invoke-Checked([string]$Executable,[string[]]$Arguments) {
        Assert ($Executable -eq (Join-Path $script:Runtime 'uv/uv.exe')) 'Unexpected executable'
        Assert ($env:UV_PYTHON_CPYTHON_BUILD -eq '20261021') 'Wrong Python build override'
        if ($Arguments[0] -eq 'python') {
            Write-Text (Join-Path $script:Managed 'BUILD') '20261021'
            Write-Text (Join-Path $script:Runtime 'python/marker') 'NEW PYTHON'
        }
        if ($Arguments[0] -eq 'sync') { Write-Text (Join-Path $script:Runtime 'venv/marker') 'NEW VENV' }
        if ($Arguments[0] -eq $script:PythonMode) { throw ('SYNTHETIC ' + $script:PythonMode + ' failure') }
    }
    Write-Text (Join-Path $script:Root 'data/mysql/sentinel') 'EXISTING DATABASE'
    Write-Text (Join-Path $script:Runtime 'python/marker') 'OLD PYTHON'
    Write-Text (Join-Path $script:Runtime 'venv/marker') 'OLD VENV'
    $oldBuildEnv=$env:UV_PYTHON_CPYTHON_BUILD
    foreach ($mode in @('python','audit','sync','pip')) {
        $script:PythonMode=$mode
        Reject { Install-PythonRuntime $pin } 'se restauro la pareja anterior'
        Assert ([IO.File]::ReadAllText((Join-Path $script:Runtime 'python/marker')) -eq 'OLD PYTHON') ('Lost Python after ' + $mode)
        Assert ([IO.File]::ReadAllText((Join-Path $script:Runtime 'venv/marker')) -eq 'OLD VENV') ('Lost venv after ' + $mode)
        Assert ($env:UV_PYTHON_CPYTHON_BUILD -eq $oldBuildEnv) 'Install leaked process environment'
    }
    $script:PythonMode='ok'; $script:FailPythonBackup=$true
    Reject { Install-PythonRuntime $pin } 'se restauro la pareja anterior'
    Assert ([IO.File]::ReadAllText((Join-Path $script:Runtime 'venv/marker')) -eq 'OLD VENV') 'Partial backup move lost the old venv'
    $script:ProbeVersion='3.12.14'
    Reject { Install-PythonRuntime $pin } 'version x64 y runtime propios'
    Assert ([IO.File]::ReadAllText((Join-Path $script:Runtime 'python/marker')) -eq 'OLD PYTHON') 'Version mismatch did not restore interpreter'
    $script:ProbeVersion='3.12.15'; $script:ProbeBits=32
    Reject { Install-PythonRuntime $pin } 'version x64 y runtime propios'
    $script:ProbeBits=64
    $script:ProbeBase=Join-Path $script:Root 'foreign-python'
    Reject { Install-PythonRuntime $pin } 'version x64 y runtime propios'
    Assert ([IO.File]::ReadAllText((Join-Path $script:Runtime 'venv/marker')) -eq 'OLD VENV') 'Foreign base interpreter did not restore the previous venv'
    $script:ProbeBase=$script:Managed
    Install-PythonRuntime $pin
    Assert ([IO.File]::ReadAllText((Join-Path $script:Runtime 'python/marker')) -eq 'NEW PYTHON') 'New interpreter not active'
    Assert ([IO.File]::ReadAllText((Join-Path $script:Runtime 'venv/marker')) -eq 'NEW VENV') 'New venv not active'
    Assert ([IO.File]::ReadAllText((Join-Path $script:Root 'data/mysql/sentinel')) -eq 'EXISTING DATABASE') 'Runtime update touched database files'
    Assert (@(Get-ChildItem -LiteralPath $script:Runtime -Directory -Filter 'python-previous-*' | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'venv/marker') }).Count -eq 1) 'Old complete Python pair not retained'
    Write-Text (Join-Path $script:Managed 'BUILD') '20261003'
    Reject { Assert-PythonRuntime $pin } 'build fijado'

    # A sibling WAMP path or the chosen path in service arguments is not ownership.
    $wamp=Join-Path $script:Root 'wamp ! (&)'
    $foreign=Join-Path ($wamp + '-other') 'bin/apache/bin/httpd.exe'
    $own=Join-Path $wamp 'bin/apache/bin/httpd.exe'
    $script:Services=@([pscustomobject]@{Name='foreign';PathName=('"' + $foreign + '" -f "' + $wamp + '/config"')})
    function Get-CimInstance { param($ClassName); return $script:Services }
    Reject { Find-Apache $wamp } 'unico servicio Apache'
    $script:Services+= [pscustomobject]@{Name='own';PathName=('"' + $own + '" -k runservice')}
    Assert ((Find-Apache $wamp).service -eq 'own') 'Service path boundary selected a foreign Apache'
    Write-Host 'SYNTHETIC PASS: upgrades, rollback, downgrade/tamper refusal, Python pair failures, build/version checks, path punctuation and Apache ownership'
} finally { if (Test-Path -LiteralPath $script:Root) { Remove-Item -LiteralPath $script:Root -Recurse -Force } }
