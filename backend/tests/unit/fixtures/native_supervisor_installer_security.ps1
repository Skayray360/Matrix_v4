# Creado por Aldo Garcia.
# Descargas y ejecutables simulados: no instala componentes del sistema.
param([Parameter(Mandatory=$true)][string]$Controller)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Controller,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'AST parse failed' }
foreach ($function in $ast.FindAll({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] },$false)) { Invoke-Expression $function.Extent.Text }
$script:Root=Join-Path ([IO.Path]::GetTempPath()) ('matrix installer security ' + [guid]::NewGuid().ToString('N'))
$script:Runtime=Join-Path $script:Root 'runtime'
$script:Config=Join-Path $script:Root 'config'
$script:Utf8=[Text.UTF8Encoding]::new($false)
function Assert([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Reject([scriptblock]$Body,[string]$Pattern) {
    $message=''
    try { & $Body | Out-Null } catch { $message=$_.Exception.Message }
    Assert ($message -match $Pattern) ('Unexpected rejection: ' + $message)
}
try {
    foreach ($directory in @($script:Runtime,$script:Config)) { Ensure-Directory $directory }
    foreach ($name in @('UV_AUDIT_IGNORE','UV_FIND_LINKS','NODE_OPTIONS','NPM_CONFIG_USERCONFIG','PYTHONPATH')) {
        $old=[Environment]::GetEnvironmentVariable($name,'Process')
        try {
            [Environment]::SetEnvironmentVariable($name,'SYNTHETIC PRIVATE VALUE','Process')
            Reject { Assert-InstallerEnvironment } 'variable heredada'
        } finally { [Environment]::SetEnvironmentVariable($name,$old,'Process') }
    }
    # secrets-scan: allow (URL con credenciales ficticias; verifica rechazo antes de acceder a red)
    foreach ($url in @('http://github.com/fixture.zip','https://example.invalid/fixture.zip','https://github.com:444/fixture.zip','https://user:secret@github.com/fixture.zip')) {
        Reject { Download-Pinned $url ('a'*64) (Join-Path $script:Root 'never-download.zip') } 'proveedor no autorizado'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    foreach ($name in @('../outside','..\outside','C:\outside')) {
        $archive=Join-Path $script:Root ([guid]::NewGuid().ToString('N') + '.zip')
        $zip=[IO.Compression.ZipFile]::Open($archive,[IO.Compression.ZipArchiveMode]::Create)
        try { $zip.CreateEntry($name) | Out-Null } finally { $zip.Dispose() }
        Reject { Expand-Pinned $archive (Join-Path $script:Root 'unpacked') } 'Ruta no valida'
    }
    $source=Join-Path $script:Root 'source'
    Write-Text (Join-Path $source 'node.exe') 'INERT NODE'
    Write-Text (Join-Path $source 'node_modules/npm/bin/npm-cli.js') 'INERT NPM'
    $archive=Join-Path $script:Runtime 'downloads/node-fixture.zip'
    Ensure-Directory ([IO.Path]::GetDirectoryName($archive))
    [IO.Compression.ZipFile]::CreateFromDirectory($source,$archive)
    $pin=[pscustomobject]@{version='fixture';url='https://nodejs.org/fixture.zip';sha256=(Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()}
    $exe=Join-Path $script:Runtime 'node/node.exe'
    Install-ZipRuntime 'node' $pin $exe
    Remove-Item -LiteralPath $archive
    Install-ZipRuntime 'node' $pin $exe
    Write-Text (Join-Path $script:Runtime 'node/node_modules/npm/bin/npm-cli.js') 'ALTERED NPM'
    Reject { Install-ZipRuntime 'node' $pin $exe } 'Integridad incorrecta'
    # El Node global solo se acepta por hash de ejecutable Y del bundle npm.
    $script:Candidate=Join-Path $script:Root 'global-node/node.exe'
    Write-Text $script:Candidate 'VERIFIED NODE FIXTURE'
    $npm=Join-Path $script:Root 'global-node/node_modules/npm'
    Write-Text (Join-Path $npm 'bin/npm-cli.js') 'VERIFIED NPM FIXTURE'
    $nodePin=[pscustomobject]@{executable_sha256=(Get-FileHash $script:Candidate -Algorithm SHA256).Hash;npm_tree_sha256=(Get-TreeDigest $npm)}
    function Get-Command { return [pscustomobject]@{Source=$script:Candidate} }
    Assert ((Find-VerifiedNode $nodePin) -eq (Split-Path -Parent $script:Candidate)) 'Verified global Node was not reused'
    Write-Text (Join-Path $npm 'bin/npm-cli.js') 'ALTERED NPM'
    Assert (-not (Find-VerifiedNode $nodePin)) 'Global Node with altered npm was accepted'
    # Visual C++: detection, signature before execution, no downgrade/reboot.
    $script:Installed=[version]'14.51.36247.0'
    $script:Downloads=0; $script:Executions=0; $script:ExitCode=0
    $script:SignatureStatus='Valid'; $script:Publisher='CN=Microsoft Corporation, O=Microsoft Corporation, C=US'
    Write-Text (Join-Path $script:Config 'runtime-manifest.json') '{"vcredist":{"version":"14.51.36247.0","url":"https://download.visualstudio.microsoft.com/fixture","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}'
    function Get-VcRuntimeVersion { return $script:Installed }
    function Download-Pinned { $script:Downloads++ }
    function Get-AuthenticodeSignature { return [pscustomobject]@{Status=$script:SignatureStatus;SignerCertificate=[pscustomobject]@{Subject=$script:Publisher}} }
    function Start-Process {
        param($FilePath,$ArgumentList,[switch]$Wait,[switch]$PassThru)
        Assert ($ArgumentList -contains '/norestart' -and $ArgumentList -contains '/quiet') 'Unexpected system installer arguments'
        $script:Executions++; $script:Installed=[version]'14.51.36247.0'
        return [pscustomobject]@{ExitCode=$script:ExitCode}
    }
    Install-VcRuntime
    Assert ($script:Downloads -eq 0 -and $script:Executions -eq 0) 'Existing Visual C++ was reinstalled'
    $script:Installed=[version]'14.52.0.0'
    Install-VcRuntime
    Assert ($script:Downloads -eq 0) 'Newer Visual C++ was downgraded'
    $script:Installed=$null; $script:SignatureStatus='NotSigned'
    Reject { Install-VcRuntime } 'firma Microsoft valida'
    Assert ($script:Executions -eq 0) 'Unsigned installer was executed'
    $script:SignatureStatus='Valid'; $script:Publisher='O=Different Publisher'
    Reject { Install-VcRuntime } 'firma Microsoft valida'
    Assert ($script:Executions -eq 0) 'Wrong publisher was executed'
    $script:Publisher='O=Microsoft Corporation, C=US'
    Install-VcRuntime
    Assert ($script:Executions -eq 1) 'Missing Visual C++ was not installed'
    $script:Installed=$null; $script:ExitCode=3010
    Reject { Install-VcRuntime } 'requiere reiniciar'
    # La version externa se consulta sin enviar documentos ni credenciales.
    function Local-OllamaUrl { return 'http://127.0.0.1:11434' }
    $script:AuditMode='ok'
    function Invoke-RestMethod {
        param($Uri,$Method,$ContentType,$Body,$TimeoutSec,$MaximumRedirection)
        Assert ($MaximumRedirection -eq 0) 'Security probes must reject redirects'
        if ($Uri -eq 'http://127.0.0.1:11434/api/version') { return [pscustomobject]@{version='0.40.6'} }
        Assert ($Uri -eq 'https://api.osv.dev/v1/query' -and $Method -eq 'Post') 'Unexpected audit destination'
        $payload=$Body | ConvertFrom-Json
        Assert ($payload.package.name -eq 'github.com/ollama/ollama' -and $payload.version -eq 'v0.40.6') 'Wrong package identity'
        if ($script:AuditMode -eq 'offline') { throw 'synthetic network failure' }
        if ($script:AuditMode -eq 'vulnerable') { return [pscustomobject]@{vulns=@([pscustomobject]@{id='SYNTHETIC-ADVISORY'})} }
        if ($script:AuditMode -eq 'malformed') { return 'invalid response' }
        return [pscustomobject]@{}
    }
    Assert-OllamaSecurity
    $script:AuditMode='vulnerable'
    Reject { Assert-OllamaSecurity } 'SYNTHETIC-ADVISORY'
    $script:AuditMode='offline'
    Reject { Assert-OllamaSecurity } 'No se pudo completar'
    $script:AuditMode='malformed'
    Reject { Assert-OllamaSecurity } 'no valida'
    Write-Host 'SYNTHETIC PASS: environment, origins, ZIP traversal, reuse, tamper detection, signed Visual C++ and external audit failures'

} finally { if (Test-Path -LiteralPath $script:Root) { Remove-Item -LiteralPath $script:Root -Recurse -Force } }
