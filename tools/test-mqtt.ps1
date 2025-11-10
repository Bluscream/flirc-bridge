Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TopicFilter = '#'
$SearchPattern = '*flirc*'
$TimeoutSeconds = 10
$OutputFileName = 'mqtt.csv'

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

function Import-DotEnvFile {
    param([string]$Path)

    if (-not (Test-Path -Path $Path)) {
        Write-Verbose ("No .env file found at {0}" -f $Path)
        return $false
    }

    try {
        Write-Verbose ("Loading environment variables from {0}" -f $Path)
        $result = Set-DotEnv -Path $Path -ReturnVars
        return ($null -ne $result)
    }
    catch {
        Write-Warning ("Failed to load {0}: {1}" -f $Path, $_)
        return $false
    }
}

function Receive-MqttMessages {
    param(
        [Parameter(Mandatory)] [uPLibrary.Networking.M2Mqtt.MqttClient]$Session,
        [Parameter(Mandatory)] [string]$Topic,
        [int]$TimeoutSeconds = 0
    )

    $sourceId = [Guid]::NewGuid().ToString()
    $messages = New-Object System.Collections.Generic.List[object]

    try {
        Register-ObjectEvent -InputObject $Session -EventName MqttMsgPublishReceived -SourceIdentifier $sourceId | Out-Null
        $null = $Session.Subscribe($Topic, 0)
        Write-Verbose ("Subscribed to {0}" -f $Topic)

        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

        while ($Session.IsConnected -and (Get-EventSubscriber -SourceIdentifier $sourceId)) {
            if ($TimeoutSeconds -gt 0 -and $stopwatch.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                Write-Verbose ("Sampling timeout ({0}s) reached" -f $TimeoutSeconds)
                break
            }

            try {
                Get-Event -SourceIdentifier $sourceId -ErrorAction Stop | ForEach-Object {
                    $topic = $null
                    if ($_.SourceEventArgs.PSObject.Properties["Topic"]) {
                        $topic = [string]$_.SourceEventArgs.Topic
                    }
                    elseif ($_.SourceEventArgs.PSObject.Properties["e"]) {
                        $topic = [string]$_.SourceEventArgs.e.Topic
                    }

                    $payloadBytes = $null
                    if ($_.SourceEventArgs.PSObject.Properties["Message"]) {
                        $payloadBytes = $_.SourceEventArgs.Message
                    }
                    elseif ($_.SourceEventArgs.PSObject.Properties["Payload"]) {
                        $payloadBytes = $_.SourceEventArgs.Payload
                    }
                    elseif ($_.SourceEventArgs.PSObject.Properties["e"]) {
                        $payloadBytes = $_.SourceEventArgs.e.Message
                    }

                    $payload = if ($payloadBytes -is [byte[]]) {
                        [System.Text.Encoding]::UTF8.GetString($payloadBytes)
                    }
                    else {
                        [string]$payloadBytes
                    }

                    if (-not [string]::IsNullOrEmpty($topic)) {
                        $messages.Add([pscustomobject]@{ Topic = $topic; Payload = $payload })
                    }

                    Remove-Event -EventIdentifier $_.EventIdentifier
                }
            }
            catch [System.ArgumentException] {
                Start-Sleep -Milliseconds 100
            }
        }
    }
    finally {
        Write-Verbose "Cleaning up MQTT subscription"
        $null = $Session.Unsubscribe($Topic) | Out-Null
        Unregister-Event -SourceIdentifier $sourceId -ErrorAction SilentlyContinue
        Remove-Event -SourceIdentifier $sourceId -ErrorAction SilentlyContinue
    }

    return $messages
}

function Write-MqttCsv {
    param(
        [Parameter(Mandatory)] [System.Collections.IEnumerable]$Messages,
        [Parameter(Mandatory)] [string]$Path
    )

    $directory = Split-Path -Path $Path -Parent
    if ($directory -and -not (Test-Path -Path $directory)) {
        New-Item -Path $directory -ItemType Directory | Out-Null
    }

    $lines = $Messages | ForEach-Object { "{0};{1}" -f $_.Topic, $_.Payload }
    $lines | Set-Content -Encoding utf8 -Path $Path
}

$repoRoot = Split-Path $PSScriptRoot -Parent
$envPath = Join-Path $repoRoot ".env"
$defaultOutputPath = Join-Path $repoRoot $OutputFileName

Import-RequiredModule -Name "dotenv" -MinimumVersion ([Version]"0.1.0")
if (-not (Get-Command -Name "Set-DotEnv" -ErrorAction SilentlyContinue)) {
    $dotenvModuleInfo = Get-Module -Name "dotenv" -ListAvailable | Sort-Object Version -Descending | Select-Object -First 1
    if ($dotenvModuleInfo) {
        $dotenvPsm1 = Join-Path $dotenvModuleInfo.ModuleBase "dotenv.psm1"
        if (Test-Path $dotenvPsm1) {
            Import-Module -Name $dotenvPsm1 -Force
        }
    }
}
if (-not (Get-Command -Name "Set-DotEnv" -ErrorAction SilentlyContinue)) {
    Write-Verbose "Set-DotEnv not available; skipping .env import."
}
Import-RequiredModule -Name "PSMQTT" -MinimumVersion ([Version]"1.2.0")

$envLoaded = $false
if (Get-Command -Name "Set-DotEnv" -ErrorAction SilentlyContinue) {
    $envLoaded = Import-DotEnvFile -Path $envPath
}

$broker = if ($env:MQTT_BROKER) { $env:MQTT_BROKER } else { "localhost" }
$port = ConvertTo-IntOrDefault -Value $env:MQTT_PORT -Default 1883
$username = if ($env:MQTT_USERNAME) { $env:MQTT_USERNAME } else { $null }
$password = if ($env:MQTT_PASSWORD) { ConvertTo-SecureStringFromPlain $env:MQTT_PASSWORD } else { $null }
$timeoutSeconds = ConvertTo-IntOrDefault -Value $env:MQTT_TIMEOUT_SECONDS -Default $TimeoutSeconds
$outputPath = if ($env:MQTT_OUTPUT_PATH) {
    if ([System.IO.Path]::IsPathRooted($env:MQTT_OUTPUT_PATH)) { $env:MQTT_OUTPUT_PATH } else { Join-Path $repoRoot $env:MQTT_OUTPUT_PATH }
}
else {
    $defaultOutputPath
}
$useTls = $false
if ($env:MQTT_USE_TLS) {
    try { $useTls = [System.Convert]::ToBoolean($env:MQTT_USE_TLS) } catch { $useTls = $false }
}

Write-Verbose ("Environment file loaded: {0}" -f $envLoaded)
Write-Verbose ("Broker={0} Port={1} Timeout={2}s TLS={3}" -f $broker, $port, $timeoutSeconds, $useTls)

$connectParams = @{
    Hostname = $broker
    Port     = $port
}
if ($useTls) { $connectParams.TLS = $true }
if ($username -and $password) {
    $connectParams.Username = $username
    $connectParams.Password = $password
}

Write-Verbose "Connecting to MQTT broker..."
$session = Connect-MQTTBroker @connectParams

try {
    if (-not $session.IsConnected) {
        throw "Unable to establish MQTT connection."
    }

    Write-Verbose "Connection established. Collecting messages..."
    $collected = Receive-MqttMessages -Session $session -Topic $TopicFilter -TimeoutSeconds $timeoutSeconds
    Write-Verbose ("Collected {0} raw message(s)" -f $collected.Count)

    $matching = $collected | Where-Object { $_.Topic -like $SearchPattern }

    if (-not $matching) {
        Write-Output ("No topics matching '{0}' were received during the sampling window." -f $SearchPattern)
    }
    else {
        foreach ($message in $matching | Sort-Object Topic) {
            Write-Output ("{0};{1}" -f $message.Topic, $message.Payload)
        }

        Write-MqttCsv -Messages ($matching | Sort-Object Topic) -Path $outputPath
        Write-Verbose ("Wrote {0} entries to {1}" -f $matching.Count, $outputPath)
    }
}
finally {
    if ($session -ne $null -and $session.IsConnected) {
        Write-Verbose "Disconnecting MQTT session"
        Disconnect-MQTTBroker -Session $session
    }

    if ($envLoaded) {
        try {
            Remove-DotEnv -Path $envPath | Out-Null
        }
        catch {
            Write-Verbose ("Failed to unload .env variables from {0}: {1}" -f $envPath, $_)
        }
    }
}
