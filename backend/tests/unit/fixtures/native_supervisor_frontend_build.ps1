# Creado por Aldo Garcia.
# Solo funciones del controlador, archivos inertes y comandos simulados.
param([Parameter(Mandatory=$true)][string]$Controller)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Controller,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'AST parse failed' }
foreach ($function in $ast.FindAll({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] },$false)) { Invoke-Expression $function.Extent.Text }
$script:Root=Join-Path ([IO.Path]::GetTempPath()) ('matrix build test ' + [guid]::NewGuid().ToString('N'))
$script:Runtime=Join-Path $script:Root 'backend/runtime'
$script:Config=Join-Path $script:Root 'backend/config'
$script:Utf8=[Text.UTF8Encoding]::new($false)
$script:Node=(Get-Process -Id $PID).Path
$script:NodeRoot=Split-Path -Parent $script:Node
$script:Events=[Collections.Generic.List[string]]::new()
$script:CiCount=0; $script:BuildCount=0
$script:FailAudit=$false; $script:FailBuild=$false; $script:FailReceipt=$false
function Assert([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Reject([scriptblock]$Body,[string]$Pattern) {
    $message=''
    try { & $Body | Out-Null } catch { $message=$_.Exception.Message }
    Assert ($message -match $Pattern) ('Unexpected failure: ' + $message)
}
function Invoke-Python([string[]]$Arguments) {
    Assert ($Arguments -contains 'scripts.verify_supply_chain') 'Unexpected Python command'
    if ($Arguments -contains '--lock-only') { $script:Events.Add('lock') }
    else { $script:Events.Add('scan') }
}
function Invoke-Npm([string[]]$Arguments) {
    if ($Arguments[0] -eq 'audit') {
        if ($Arguments -contains 'signatures') { $script:Events.Add('signatures'); return }
        $script:Events.Add('audit')
        if ($script:FailAudit) { throw 'synthetic audit failure' }
        Assert ($Arguments -contains '--package-lock-only' -and $Arguments -contains '--audit-level=low') 'Audit must precede installation'
    } elseif ($Arguments[0] -eq 'ci') {
        $script:CiCount++; $script:Events.Add('ci')
        Write-Text (Join-Path $script:Root 'frontend/node_modules/fixture/package.json') '{"name":"fixture","version":"1.0.0"}'
    } elseif ($Arguments[0] -eq 'run') {
        $script:Events.Add('build')
        if ($script:FailBuild) { throw 'synthetic build failure' }
        $script:BuildCount++
        $target=$Arguments[[Array]::IndexOf($Arguments,'--outDir')+1]
        Assert ($target.StartsWith($script:Runtime)) 'Build must use a staging directory'
        Write-Text (Join-Path $target 'index.html') ('BUILD ' + $script:BuildCount)
        Write-Text (Join-Path $target 'assets/application.js') 'INERT BUILD'
        # El recibo debe incluir tambien el estado final generado por el build.
        Write-Text (Join-Path $script:Root 'frontend/node_modules/.vite-temp/cache') ('CACHE ' + $script:BuildCount)
    } else { throw 'Unexpected npm command' }
}
function Move-Item {
    param([string]$LiteralPath,[string]$Destination,[switch]$Force)
    if ($script:FailReceipt -and $Destination -eq (Join-Path $script:Config 'frontend-build.json')) {
        $script:FailReceipt=$false
        throw 'synthetic receipt publication failure'
    }
    Microsoft.PowerShell.Management\Move-Item @PSBoundParameters
}
try {
    foreach ($directory in @($script:Runtime,$script:Config)) { Ensure-Directory $directory }
    Write-Text (Join-Path $script:Root 'frontend/package.json') '{"name":"fixture"}'
    Write-Text (Join-Path $script:Root 'frontend/package-lock.json') '{"name":"fixture","lockfileVersion":3}'
    Write-Text (Join-Path $script:Root 'frontend/src/main.ts') 'INERT SOURCE'
    $dist=Join-Path $script:Root 'frontend/dist/index.html'
    $receipt=Join-Path $script:Config 'frontend-build.json'
    Write-Text $dist 'PREVIOUS BUILD'
    $originalPath=$env:PATH
    $script:FailAudit=$true
    Reject { Build-Frontend } 'audit failure'
    Assert ($script:CiCount -eq 0 -and $script:BuildCount -eq 0) 'Failed audit reached installation or build'
    Assert ((Get-Content -LiteralPath $dist -Raw) -eq 'PREVIOUS BUILD') 'Audit failure changed the previous build'
    Assert ($env:PATH -eq $originalPath) 'Failure did not restore PATH'
    $script:FailAudit=$false; $script:Events.Clear()
    Build-Frontend
    Assert (($script:Events -join ',') -eq 'lock,audit,ci,scan,signatures,build') 'Incorrect gate order'
    Assert-FrontendBuild
    $script:Events.Clear()
    Build-Frontend
    Assert ($script:CiCount -eq 1) 'Validated dependencies were unnecessarily reinstalled'
    Assert (($script:Events -join ',') -eq 'lock,audit,scan,signatures,build') 'Reinstallation skipped current security checks'
    $before=[IO.File]::ReadAllText($dist)
    $beforeReceipt=[IO.File]::ReadAllText($receipt)
    $script:FailBuild=$true
    Reject { Build-Frontend } 'build failure'
    Assert ([IO.File]::ReadAllText($dist) -eq $before) 'Failed compilation replaced the working build'
    $script:FailBuild=$false; $script:FailReceipt=$true
    Reject { Build-Frontend } 'receipt publication failure'
    Assert ([IO.File]::ReadAllText($dist) -eq $before) 'Failed publication did not restore the working build'
    Assert ([IO.File]::ReadAllText($receipt) -eq $beforeReceipt) 'Failed publication did not restore its receipt'
    Write-Text (Join-Path $script:Root 'frontend/node_modules/fixture/package.json') 'ALTERED'
    Build-Frontend
    Assert ($script:CiCount -eq 2) 'Altered dependencies were reused'
    Write-Text $dist 'ALTERED BUILD'
    Reject { Assert-FrontendBuild } 'Integridad incorrecta'
    Build-Frontend
    Write-Text (Join-Path $script:Root 'frontend/src/main.ts') 'NEW SOURCE'
    Reject { Assert-FrontendBuild } 'otro codigo fuente'
    Assert ($env:PATH -eq $originalPath) 'Successful build did not restore PATH'
    Write-Host 'SYNTHETIC PASS: frontend audits, reuse, repair, staged compilation, rollback and build integrity'
} finally { if (Test-Path -LiteralPath $script:Root) { Remove-Item -LiteralPath $script:Root -Recurse -Force } }
