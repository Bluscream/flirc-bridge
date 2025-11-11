param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$EnvPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Step {
    param([string]$Message)
    Write-Host "[+] $Message"
}

function Read-DotEnv {
    param([string]$Path)

    $result = @{}
    if (-not $Path -or -not (Test-Path -Path $Path)) {
        return $result
    }

    foreach ($rawLine in Get-Content -Path $Path) {
        $line = $rawLine.Trim()
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line.StartsWith('#')) { continue }
        $parts = $line -split '=', 2
        if ($parts.Length -ne 2) { continue }
        $key = $parts[0].Trim()
        $value = $parts[1].Trim()
        if (-not [string]::IsNullOrEmpty($key)) {
            $result[$key] = $value
        }
    }

    return $result
}

function Get-ConfigValue {
    param(
        [string]$Name,
        $Default = $null,
        [hashtable]$EnvValues
    )

    $envValue = [System.Environment]::GetEnvironmentVariable($Name)
    if (-not [string]::IsNullOrEmpty($envValue)) {
        return $envValue
    }
    if ($EnvValues -and $EnvValues.ContainsKey($Name)) {
        return $EnvValues[$Name]
    }
    return $Default
}

function Import-RequiredModule {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [Parameter(Mandatory)] [Version]$MinimumVersion
    )

    if (-not (Get-Module -Name $Name -ListAvailable | Where-Object { $_.Version -ge $MinimumVersion })) {
        Write-Verbose ("Installing module {0} (>= {1})" -f $Name, $MinimumVersion)
        Install-Module -Name $Name -MinimumVersion $MinimumVersion -Scope CurrentUser -Force -AllowClobber
    }

    Import-Module -Name $Name -MinimumVersion $MinimumVersion -Force
}

function ConvertTo-IntOrDefault {
    param(
        $Value,
        [int]$Default
    )

    if ($null -eq $Value -or ($Value -is [string] -and [string]::IsNullOrWhiteSpace($Value))) {
        return $Default
    }

    $parsed = 0
    if ([int]::TryParse($Value.ToString(), [ref]$parsed)) {
        return $parsed
    }

    return $Default
}

function ConvertTo-SecureStringFromPlain {
    param([Parameter(Mandatory)] [string]$Plain)

    $secure = New-Object System.Security.SecureString
    foreach ($char in $Plain.ToCharArray()) {
        $secure.AppendChar($char)
    }
    $secure.MakeReadOnly()
    return $secure
}

function ConvertTo-JsonString {
    param($Object)
    return ($Object | ConvertTo-Json -Depth 8 -Compress)
}

function Get-DynamicPropertyValue {
    param(
        [Parameter(Mandatory)] $Object,
        [Parameter(Mandatory)] [string]$Name
    )

    if ($null -eq $Object) {
        return $null
    }

    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($Name)) {
            return $Object[$Name]
        }
        return $null
    }

    $prop = $Object.PSObject.Properties[$Name]
    if ($prop) {
        return $prop.Value
    }

    return $null
}

function Invoke-AppRequest {
    param(
        [Parameter(Mandatory)] [string]$Method,
        [Parameter(Mandatory)] [string]$Path,
        [object]$Body
    )

    $methodEnum = [System.Enum]::Parse([Microsoft.PowerShell.Commands.WebRequestMethod], $Method, $true)
    $uri = "{0}{1}" -f $script:NormalizedBaseUrl, $Path

    if ($methodEnum -eq [Microsoft.PowerShell.Commands.WebRequestMethod]::Get) {
        $params = @{
            Uri         = $uri
            Method      = $methodEnum
            ContentType = 'application/json'
        }
        Write-Verbose ("HTTP {0} {1}" -f $methodEnum, $uri)
        return Invoke-RestMethod @params
    }

    $testScript = Join-Path $repoRoot "tools\test.ps1"
    $testParams = @{
        Endpoint    = $uri
        Method      = $methodEnum
        ContentType = 'application/json'
        Quiet       = $true
    }
    if ($Body) {
        $testParams.Body = $Body
        Write-Verbose ("HTTP {0} {1} -> {2}" -f $methodEnum, $uri, (ConvertTo-JsonString -Object $Body))
    }
    else {
        Write-Verbose ("HTTP {0} {1} (no body)" -f $methodEnum, $uri)
    }

    $result = & $testScript @testParams
    if ($null -ne $result.Json) {
        return $result.Json
    }
    if ($result.RawContent) {
        try {
            return $result.RawContent | ConvertFrom-Json -Depth 16
        }
        catch {
            return $result.RawContent
        }
    }
    return $null
}

function New-TestName {
    param([string]$Prefix)
    return "{0} {1}" -f $Prefix, ([Guid]::NewGuid().ToString().Substring(0, 8))
}

$repoRoot = Split-Path $PSScriptRoot -Parent
if (-not $EnvPath) {
    $EnvPath = Join-Path $repoRoot ".env"
}

$envValues = Read-DotEnv -Path $EnvPath

$script:NormalizedBaseUrl = $BaseUrl.TrimEnd('/')
$broker = Get-ConfigValue -Name "MQTT_BROKER" -Default "localhost" -EnvValues $envValues
$port = ConvertTo-IntOrDefault -Value (Get-ConfigValue -Name "MQTT_PORT" -Default 1883 -EnvValues $envValues) -Default 1883
$username = Get-ConfigValue -Name "MQTT_USERNAME" -EnvValues $envValues
$passwordPlain = Get-ConfigValue -Name "MQTT_PASSWORD" -EnvValues $envValues
$baseTopic = Get-ConfigValue -Name "MQTT_BASE_TOPIC" -Default "flirc_bridge" -EnvValues $envValues
$discoveryPrefix = Get-ConfigValue -Name "MQTT_DISCOVERY_PREFIX" -Default "homeassistant" -EnvValues $envValues
$mqttPrefix = Get-ConfigValue -Name "MQTT_PREFIX" -Default "flirc_bridge" -EnvValues $envValues
$useTlsRaw = Get-ConfigValue -Name "MQTT_USE_TLS" -EnvValues $envValues
$useTls = $false
if ($useTlsRaw) {
    try { $useTls = [System.Convert]::ToBoolean($useTlsRaw) } catch { $useTls = $false }
}

$securePassword = $null
if ($username -and $passwordPlain) {
    $securePassword = ConvertTo-SecureStringFromPlain $passwordPlain
}

$deviceId = $null
$actionId = $null
$patternId = $null
$savedPatternId = $null
$mqttSession = $null

try {
    Write-Step "Checking API status"
    $status = Invoke-AppRequest -Method Get -Path "/api/status"
    if (-not $status) {
        throw "Failed to retrieve /api/status"
    }

    Write-Step "Creating test device"
    $devicePayload = @{
        name        = New-TestName -Prefix "Test Device"
        description = "Integration test device"
    }
    $deviceResponse = Invoke-AppRequest -Method Post -Path "/api/devices" -Body $devicePayload
    $deviceId = $deviceResponse.id
    if (-not $deviceId) {
        throw "Device creation failed"
    }

    Write-Step "Creating test action"
    $actionPayload = @{
        device_id   = $deviceId
        name        = New-TestName -Prefix "Test Action"
        description = "Integration test action"
    }
    $actionResponse = Invoke-AppRequest -Method Post -Path "/api/actions" -Body $actionPayload
    $actionId = $actionResponse.id
    if (-not $actionId) {
        throw "Action creation failed"
    }

    Write-Step "Creating test pattern"
    $initialPatternData = @("0", "9000", "-4500", "560")
    $patternPayload = @{
        device_id = $deviceId
        action_id = $actionId
        patterns  = @(
            @{
                format = "raw"
                data   = $initialPatternData
                repeat = 1
                ik     = 23000
            }
        )
    }
    $patternResponse = Invoke-AppRequest -Method Post -Path "/api/pattern" -Body $patternPayload
    if (-not $patternResponse.patterns) {
        throw "Pattern creation failed"
    }
    $patternId = ($patternResponse.patterns | Select-Object -First 1).id

    Write-Step "Sending action via /api/send"
    $sendActionResponse = Invoke-AppRequest -Method Post -Path "/api/send" -Body @{ device = $deviceId; action = $actionId }
    if (-not $sendActionResponse.results) {
        throw "Action send returned no results"
    }

    Write-Step "Sending pattern directly by UUID"
    $sendPatternResponse = Invoke-AppRequest -Method Post -Path "/api/send" -Body @{ pattern = $patternId }
    if (-not $sendPatternResponse.results) {
        throw "Pattern send returned no results"
    }

    Write-Step "Sending custom pattern with save"
    $customPayload = @{
        device = $deviceId
        action = $actionId
        format = "csv"
        data   = @("4472", "552", "1664", "552")
        repeat = 1
        ik     = 23000
        save   = $true
    }
    $customSendResponse = Invoke-AppRequest -Method Post -Path "/api/send" -Body $customPayload
    if (-not $customSendResponse.results) {
        throw "Custom send returned no results"
    }
    foreach ($entry in @($customSendResponse.results)) {
        $candidateId = Get-DynamicPropertyValue -Object $entry -Name "saved_pattern_id"
        if ($candidateId) {
            $savedPatternId = $candidateId
            break
        }
    }

    Write-Step "Updating test pattern"
    $updatedPatternData = @("0", "9000", "-4500", "560", "600")
    $updatePatternPayload = @{
        device_id = $deviceId
        action_id = $actionId
        patterns  = @(
            @{
                id     = $patternId
                format = "raw"
                data   = $updatedPatternData
                repeat = 2
                ik     = 23000
            }
        )
    }
    [void](Invoke-AppRequest -Method Put -Path ("/api/pattern?pattern={0}" -f $patternId) -Body $updatePatternPayload)

    Write-Step "Updating action"
    $updatedActionPayload = @{
        name        = ($actionResponse.name + " (Updated)")
        description = "Updated by integration test"
    }
    [void](Invoke-AppRequest -Method Put -Path ("/api/actions/{0}" -f $actionId) -Body $updatedActionPayload)

    Write-Step "Updating device"
    $updatedDevicePayload = @{
        name        = ($deviceResponse.name + " (Updated)")
        description = "Updated by integration test"
    }
    [void](Invoke-AppRequest -Method Put -Path ("/api/devices/{0}" -f $deviceId) -Body $updatedDevicePayload)

    Write-Step "Triggering MQTT command"
    Import-RequiredModule -Name "PSMQTT" -MinimumVersion ([Version]"1.2.0")
    $connectParams = @{
        Hostname = $broker
        Port     = $port
    }
    if ($useTls) { $connectParams.TLS = $true }
    if ($username -and $securePassword) {
        $connectParams.Username = $username
        $connectParams.Password = $securePassword
    }
    $mqttSession = Connect-MQTTBroker @connectParams
    if (-not $mqttSession.IsConnected) {
        throw "Unable to establish MQTT connection"
    }

    try {
        $safePrefix = ($mqttPrefix -replace '-', '_')
        $safeActionId = ($actionId -replace '-', '_')
        $commandTopic = "{0}/button/{1}_{2}" -f $discoveryPrefix, $safePrefix, $safeActionId
        $pressBytes = [System.Text.Encoding]::UTF8.GetBytes("PRESS")
        [void]$mqttSession.Publish($commandTopic, $pressBytes, 0, $false)

        $mqttPayload = @{
            format = "raw"
            data   = @("+9000", "-4500", "600")
            repeat = 1
            ik     = 23000
        }
        $sendTopic = "{0}/send" -f $baseTopic
        $payloadBytes = [System.Text.Encoding]::UTF8.GetBytes((ConvertTo-JsonString $mqttPayload))
        [void]$mqttSession.Publish($sendTopic, $payloadBytes, 0, $false)
    }
    finally {
        if ($mqttSession -and $mqttSession.IsConnected) {
            Disconnect-MQTTBroker -Session $mqttSession
            $mqttSession = $null
        }
    }

    if ($savedPatternId) {
        Write-Step "Deleting saved custom pattern"
        [void](Invoke-AppRequest -Method Delete -Path ("/api/pattern?pattern={0}" -f $savedPatternId))
        $savedPatternId = $null
    }

    if ($patternId) {
        Write-Step "Deleting primary test pattern"
        [void](Invoke-AppRequest -Method Delete -Path ("/api/pattern?pattern={0}" -f $patternId))
        $patternId = $null
    }

    if ($actionId) {
        Write-Step "Deleting test action"
        [void](Invoke-AppRequest -Method Delete -Path ("/api/actions/{0}" -f $actionId))
        $actionId = $null
    }

    if ($deviceId) {
        Write-Step "Deleting test device"
        [void](Invoke-AppRequest -Method Delete -Path ("/api/devices/{0}" -f $deviceId))
        $deviceId = $null
    }

    Write-Host "All integration tests completed successfully." -ForegroundColor Green
}
catch {
    Write-Error $_
    exit 1
}
finally {
    if ($savedPatternId) {
        try { [void](Invoke-AppRequest -Method Delete -Path ("/api/pattern?pattern={0}" -f $savedPatternId)) } catch {}
    }
    if ($patternId) {
        try { [void](Invoke-AppRequest -Method Delete -Path ("/api/pattern?pattern={0}" -f $patternId)) } catch {}
    }
    if ($actionId) {
        try { [void](Invoke-AppRequest -Method Delete -Path ("/api/actions/{0}" -f $actionId)) } catch {}
    }
    if ($deviceId) {
        try { [void](Invoke-AppRequest -Method Delete -Path ("/api/devices/{0}" -f $deviceId)) } catch {}
    }
    if ($mqttSession -and $mqttSession.IsConnected) {
        try { Disconnect-MQTTBroker -Session $mqttSession } catch {}
    }
}
