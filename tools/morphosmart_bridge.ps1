param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("health", "enroll", "identify", "delete")]
    [string]$Action,

    [string]$PayloadJson = "{}",
    [string]$SdkDir = "C:\Program Files\Morpho\MorphoManager\Client",
    [string]$DeviceSerial = "",
    [string]$ManagerUsername = "",
    [string]$ManagerPassword = "",
    [string]$Finger = "RightIndex",
    [string]$Threshold = "FAR_5",
    [int]$TimeoutSeconds = 20,
    [string]$ProgressJsonPath = "",
    [string]$PreviewImagePath = ""
)

$ErrorActionPreference = "Stop"

function Write-BridgeJson {
    param([hashtable]$Payload)
    Write-Output ($Payload | ConvertTo-Json -Compress -Depth 6)
}

function Exit-WithBridgeJson {
    param(
        [hashtable]$Payload,
        [int]$Code = 0
    )
    Write-BridgeJson -Payload $Payload
    exit $Code
}

function ConvertTo-BridgeHashtable {
    param([object]$Value)

    if ($null -eq $Value) {
        return @{}
    }
    if ($Value -is [System.Collections.IDictionary]) {
        return $Value
    }

    $result = @{}
    foreach ($property in $Value.PSObject.Properties) {
        $result[$property.Name] = $property.Value
    }
    return $result
}

function Get-HashtableValue {
    param(
        [hashtable]$Hashtable,
        [string]$Key,
        $Default = ""
    )

    if ($Hashtable.ContainsKey($Key)) {
        return $Hashtable[$Key]
    }
    return $Default
}

function Write-EnrollmentProgress {
    param(
        [string]$State,
        [string]$Message,
        [string]$Instruction = "",
        [int]$AcquisitionNumber = 0,
        [int]$Quality = 0,
        [bool]$Complete = $false,
        [bool]$Success = $false,
        [string]$TemplateRef = ""
    )

    if ([string]::IsNullOrWhiteSpace($ProgressJsonPath)) {
        return
    }

    $existing = @{}
    if (Test-Path -LiteralPath $ProgressJsonPath) {
        try {
            $existing = ConvertTo-BridgeHashtable -Value (Get-Content -Path $ProgressJsonPath -Raw | ConvertFrom-Json)
        } catch {
            $existing = @{}
        }
    }

    $resolvedInstruction = if (-not [string]::IsNullOrWhiteSpace($Instruction)) {
        $Instruction
    } else {
        [string](Get-HashtableValue -Hashtable $existing -Key "instruction")
    }
    $resolvedAcquisitionNumber = if ($AcquisitionNumber -gt 0) {
        $AcquisitionNumber
    } else {
        [int](Get-HashtableValue -Hashtable $existing -Key "acquisition_number" -Default 0)
    }
    $resolvedQuality = if ($Quality -gt 0) {
        $Quality
    } else {
        [int](Get-HashtableValue -Hashtable $existing -Key "quality" -Default 0)
    }

    $payload = @{
        state = $State
        message = $Message
        instruction = $resolvedInstruction
        acquisition_number = $resolvedAcquisitionNumber
        quality = $resolvedQuality
        complete = $Complete
        success = $Success
        template_ref = $TemplateRef
        preview_available = -not [string]::IsNullOrWhiteSpace($PreviewImagePath) -and (Test-Path -LiteralPath $PreviewImagePath)
        updated_at = [datetime]::UtcNow.ToString("o")
    }

    $directory = Split-Path -Parent $ProgressJsonPath
    if (-not [string]::IsNullOrWhiteSpace($directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    [System.IO.File]::WriteAllText(
        $ProgressJsonPath,
        ($payload | ConvertTo-Json -Depth 6),
        [System.Text.Encoding]::UTF8
    )
}

function Enable-MorphoSdkProcessPath {
    param([string]$SdkRoot)

    $currentPath = [string]$env:PATH
    $segments = @()
    if (-not [string]::IsNullOrWhiteSpace($currentPath)) {
        $segments = $currentPath -split ";"
    }

    if ($segments -contains $SdkRoot) {
        return
    }

    $env:PATH = if ([string]::IsNullOrWhiteSpace($currentPath)) {
        $SdkRoot
    } else {
        "$SdkRoot;$currentPath"
    }
}

function Invoke-MorphoManagerProbe {
    param(
        [string]$ProbePath,
        [string[]]$Arguments
    )

    $probeLines = @(& $ProbePath @Arguments 2>&1)
    $probeExitCode = $LASTEXITCODE
    $probeMap = @{}
    foreach ($line in $probeLines) {
        $text = [string]$line
        if ([string]::IsNullOrWhiteSpace($text)) {
            continue
        }
        $trimmed = $text.Trim()
        if ($trimmed -match "^(?<key>[^=]+)=(?<value>.*)$") {
            $probeMap[$Matches.key] = $Matches.value
        }
    }

    return @{
        exit_code = $probeExitCode
        lines = $probeLines
        map = $probeMap
    }
}

function New-BridgeDelegate {
    param(
        [type]$DelegateType,
        [string]$MethodName
    )

    $methodInfo = [MorphoBridgeHub].GetMethod($MethodName)
    if ($null -eq $methodInfo) {
        throw "Callback method '$MethodName' was not found."
    }
    return [System.Delegate]::CreateDelegate($DelegateType, $methodInfo)
}

function Reset-BridgeHub {
    [MorphoBridgeHub]::Reset()
}

function Wait-BridgeResponse {
    param([string]$OperationName)

    if (-not [MorphoBridgeHub]::Done.Wait($TimeoutSeconds * 1000)) {
        throw "$OperationName timed out after $TimeoutSeconds seconds."
    }
    if ($null -ne [MorphoBridgeHub]::AsyncException) {
        throw [MorphoBridgeHub]::AsyncException
    }

    $response = [MorphoBridgeHub]::Response
    if ($null -eq $response) {
        throw "No response was returned for $OperationName."
    }
    return $response
}

function Get-MorphoResponseSummary {
    param([object]$Response)

    $status = if ($null -ne $Response.Status) { $Response.Status.ToString() } else { "" }
    return @{
        status = $status
        error_message = $Response.ErrorMessage
        device_serial = $Response.DeviceSerialNumber
        operation_complete = [bool]$Response.OperationComplete
    }
}

function Assert-MorphoSuccess {
    param(
        [object]$Response,
        [string]$OperationName
    )

    if ($Response.Status.ToString() -ne "Success") {
        $message = $Response.ErrorMessage
        if ([string]::IsNullOrWhiteSpace($message)) {
            $message = "$OperationName failed with SDK status '$($Response.Status)'."
        }
        throw $message
    }
}

function Invoke-MorphoEnumeration {
    Reset-BridgeHub
    $requestId = [guid]::NewGuid()
    $callback = New-BridgeDelegate `
        -DelegateType ([System.Action[ID1.MorphoSmart.EnumerateDevicesMorphoSmartResponse]]) `
        -MethodName "HandleEnumerate"
    [ID1.MorphoSmart.MorphoSmart]::EnumerateDevices($requestId, $callback)
    return Wait-BridgeResponse -OperationName "Device enumeration"
}

function Resolve-MorphoDevice {
    if (-not [string]::IsNullOrWhiteSpace($DeviceSerial)) {
        return [pscustomobject]@{
            serial = $DeviceSerial
            description = ""
            enumeration = $null
        }
    }

    $enumeration = Invoke-MorphoEnumeration
    Assert-MorphoSuccess -Response $enumeration -OperationName "Device enumeration"
    if ($null -eq $enumeration.Devices -or $enumeration.Devices.Count -lt 1) {
        throw "The Morpho SDK did not return any connected fingerprint devices."
    }

    $device = $enumeration.Devices[0]
    return [pscustomobject]@{
        serial = $device.SerialNumber
        description = $device.Description
        enumeration = $enumeration
    }
}

function Invoke-MorphoGetUserMonikers {
    param([string]$TargetSerial)

    Reset-BridgeHub
    $requestId = [guid]::NewGuid()
    $callback = New-BridgeDelegate `
        -DelegateType ([System.Action[ID1.MorphoSmart.GetUserMonikersMorphoSmartResponse]]) `
        -MethodName "HandleGetUserMonikers"
    [ID1.MorphoSmart.MorphoSmart]::GetUserMonikers(
        $requestId,
        $TargetSerial,
        $callback
    )
    return Wait-BridgeResponse -OperationName "List enrolled users"
}

function Invoke-MorphoTemplateCapture {
    param(
        [string]$TargetSerial,
        [ID1.MorphoSmart.Finger]$TargetFinger
    )

    $requirements = New-Object ID1.MorphoSmart.TemplateCaptureRequirements
    $requirements.ComputerTemplateCoding_Morpho_PkCompV2_Required = $true

    Reset-BridgeHub
    $requestId = [guid]::NewGuid()
    $callback = New-BridgeDelegate `
        -DelegateType ([System.Action[ID1.MorphoSmart.TemplateCaptureMorphoSmartResponse]]) `
        -MethodName "HandleTemplateCapture"
    [ID1.MorphoSmart.MorphoSmart]::TemplateCapture(
        $requestId,
        $TargetSerial,
        $requirements,
        $TargetFinger,
        $false,
        $callback
    )
    return Wait-BridgeResponse -OperationName "Template capture"
}

function Invoke-MorphoAddUser {
    param(
        [string]$TargetSerial,
        [guid]$UserId,
        [byte[]]$Template,
        [byte[]]$Data
    )

    $user = [ID1.MorphoSmart.MorphoSmartUser]::new(
        $UserId,
        [ID1.MorphoSmart.TemplateFormat]::MorphoPKCompV2,
        $Template,
        $Data
    )

    Reset-BridgeHub
    $requestId = [guid]::NewGuid()
    $callback = New-BridgeDelegate `
        -DelegateType ([System.Action[ID1.MorphoSmart.MorphoSmartResponse]]) `
        -MethodName "HandleGeneric"
    [ID1.MorphoSmart.MorphoSmart]::AddUser(
        $requestId,
        $TargetSerial,
        $user,
        $false,
        $callback
    )
    return Wait-BridgeResponse -OperationName "Add user"
}

function New-MorphoReferenceUsers {
    param([object[]]$Candidates)

    $users = [System.Collections.Generic.List[ID1.MorphoSmart.MorphoSmartUser]]::new()
    foreach ($candidate in $Candidates) {
        $candidateMap = ConvertTo-BridgeHashtable -Value $candidate
        $templateRef = [string](Get-HashtableValue -Hashtable $candidateMap -Key "template_ref")
        $templateFormatName = [string](Get-HashtableValue -Hashtable $candidateMap -Key "template_format" -Default "MorphoPKCompV2")
        $templateDataBase64 = [string](Get-HashtableValue -Hashtable $candidateMap -Key "template_data_base64")
        if ([string]::IsNullOrWhiteSpace($templateRef) -or [string]::IsNullOrWhiteSpace($templateDataBase64)) {
            continue
        }

        $templateFormat = [System.Enum]::Parse([ID1.MorphoSmart.TemplateFormat], $templateFormatName, $true)
        $templateBytes = [System.Convert]::FromBase64String($templateDataBase64)
        $userId = [guid]$templateRef
        $users.Add([ID1.MorphoSmart.MorphoSmartUser]::new($userId, $templateFormat, $templateBytes))
    }
    return $users
}

function Invoke-MorphoCaptureAndVerify {
    param(
        [string]$TargetSerial,
        [ID1.MorphoSmart.MatchThresholdEnum]$TargetThreshold,
        [System.Collections.Generic.List[ID1.MorphoSmart.MorphoSmartUser]]$ReferenceUsers
    )

    Reset-BridgeHub
    $requestId = [guid]::NewGuid()
    $callback = New-BridgeDelegate `
        -DelegateType ([System.Action[ID1.MorphoSmart.CaptureAndVerifyMorphoSmartResponse]]) `
        -MethodName "HandleCaptureAndVerify"
    [ID1.MorphoSmart.MorphoSmart]::CaptureAndVerify(
        $requestId,
        $TargetSerial,
        [ID1.MorphoSmart.SampleCaptureRequirmentsEnum]::MorphoPKCompV2,
        $false,
        $TargetThreshold,
        $ReferenceUsers,
        $callback
    )
    return Wait-BridgeResponse -OperationName "Capture and verify"
}

function Resolve-BridgeErrorMessage {
    param(
        [string]$Operation,
        [string]$Message
    )

    if ([string]::IsNullOrWhiteSpace($Message)) {
        return $Message
    }

    if ($Message -like "*Base not found*") {
        switch ($Operation) {
            "enroll" {
                return "The scanner produced a fingerprint preview, but its internal base is unavailable. The attendance app now stores templates locally, so close the running app completely, start it again, and retry enrollment."
            }
            "identify" {
                return "The scanner internal base is unavailable. Restart the attendance app so matching can continue against the templates stored in this system."
            }
        }
    }

    return $Message
}

$script:MorphoAssemblyResolverRegistered = $false

function Register-MorphoAssemblyResolver {
    param([string]$SdkRoot)

    if ($script:MorphoAssemblyResolverRegistered) {
        return
    }

    $script:MorphoAssemblyResolverRegistered = $true
    [System.AppDomain]::CurrentDomain.add_AssemblyResolve({
        param($sender, $eventArgs)

        $simpleName = ([System.Reflection.AssemblyName]::new($eventArgs.Name)).Name + ".dll"
        $candidate = Join-Path $SdkRoot $simpleName
        if (Test-Path -LiteralPath $candidate) {
            return [System.Reflection.Assembly]::LoadFrom($candidate)
        }
        return $null
    }.GetNewClosure())
}

function Load-MorphoManagerAssemblies {
    param([string]$SdkRoot)

    Register-MorphoAssemblyResolver -SdkRoot $SdkRoot

    $assemblyNames = @(
        "P6.dll",
        "P6.Client.dll",
        "ID1.MM.Client.Common.dll"
    )
    foreach ($fileName in $assemblyNames) {
        $fullPath = Join-Path $SdkRoot $fileName
        if (-not (Test-Path -LiteralPath $fullPath)) {
            throw "Required MorphoManager client assembly is missing: $fullPath"
        }
        [void][System.Reflection.Assembly]::LoadFrom($fullPath)
    }
}

function Initialize-MorphoManagerContext {
    $init = [ID1.MM.Client.Common.InitializationArguments]::new()
    [P6.RuntimeContext]::Initialize(
        [P6.RuntimeEnvironmentEnum]::Console,
        [P6.RuntimePurposeEnum]::Release,
        $init.ProductID,
        $init.ProductName,
        $init
    )
    [P6.Client.RuntimeContext]::Initialize($init)
    return $init
}

function Get-MorphoManagerConfig {
    param([string]$SdkRoot)

    return [P6.Client.InstanceContextConfiguration]::Load($SdkRoot)
}

function New-MorphoManagerSessionClient {
    param(
        [ID1.MM.Client.Common.InitializationArguments]$Initialization,
        [P6.Client.InstanceContextConfiguration]$Config
    )

    return $Initialization.CreateSessionServiceClient(
        $Config.TLSMode,
        $Config.ServerHostname,
        $Config.ServerPort,
        [P6.Client.RuntimeContext]::ServiceName,
        $Config.WebServiceProtocol,
        $Config.ServerCertificateAuthenticationMode,
        $Config.ServerCertificateThumbprint,
        $Config.ServerCertificateIssuerThumbprint,
        $Config.ClientCertificateThumbprint,
        $Config.CertificateRevocationCheckMode
    )
}

function New-MorphoManagerFunctionClient {
    param(
        [ID1.MM.Client.Common.InitializationArguments]$Initialization,
        [P6.Client.InstanceContextConfiguration]$Config
    )

    return $Initialization.CreateFunctionServiceClient(
        $Config.TLSMode,
        $Config.ServerHostname,
        $Config.ServerPort,
        [P6.Client.RuntimeContext]::ServiceName,
        $Config.WebServiceProtocol,
        $Config.ServerCertificateAuthenticationMode,
        $Config.ServerCertificateThumbprint,
        $Config.ServerCertificateIssuerThumbprint,
        $Config.ClientCertificateThumbprint,
        $Config.CertificateRevocationCheckMode
    )
}

function New-MorphoManagerCredential {
    param(
        [string]$Username,
        [string]$Password
    )

    $credential = [MorphoManager.UsernamePasswordCredential]::new()
    $credential.Username = $Username
    $credential.Password = $Password
    $credential.ConsumerComponentName = "AttendanceSystem"
    $credential.InstanceHostname = $env:COMPUTERNAME
    $credential.InstanceID = [guid]::NewGuid()
    $credential.ConsumerVersion = [version]"1.0.0.0"
    $credential.ConsumerTimeZoneID = [System.TimeZoneInfo]::Local.Id
    $credential.ConsumerLanguageCode = [System.Globalization.CultureInfo]::CurrentCulture.Name
    return $credential
}

function Invoke-MorphoManagerLogin {
    param(
        [ID1.MM.Client.Common.InitializationArguments]$Initialization,
        $SessionClient,
        [string]$Username,
        [string]$Password
    )

    $credential = New-MorphoManagerCredential -Username $Username -Password $Password
    return $Initialization.SessionServiceLogin($SessionClient, $credential)
}

function Invoke-MorphoManagerInventory {
    param(
        [ID1.MM.Client.Common.InitializationArguments]$Initialization,
        $FunctionClient,
        [P6.Client.Sessions.SessionToken]$SessionToken
    )

    $arguments = [MorphoManager.GetBiometricDeviceInventoryFunctionArguments]::new()
    $arguments.Source = [MorphoManager.RequestSource]::BiometricDevicePage
    return $Initialization.FunctionServiceRun($FunctionClient, $SessionToken, $arguments)
}

function Get-MorphoManagerHealth {
    param([string]$SdkRoot)

    $health = @{
        status = "unavailable"
        details = ""
        server_hostname = ""
        server_port = 0
        webservice_protocol = ""
        tls_mode = ""
        cached_credential_bytes = 0
        session_client_created = $false
        function_client_created = $false
        credential_source = "none"
        login_status = "not_attempted"
        session = @{}
        inventory = @{}
        registered_device_count = 0
        registered_devices = @()
    }

    try {
        $probePath = Join-Path $PSScriptRoot "morphomanager_probe.exe"
        if (-not (Test-Path -LiteralPath $probePath)) {
            throw "MorphoManager probe executable was not found at $probePath"
        }

        $probeArgs = @("config")
        if (-not [string]::IsNullOrWhiteSpace($ManagerUsername) -or -not [string]::IsNullOrWhiteSpace($ManagerPassword)) {
            $probeArgs += @($ManagerUsername, $ManagerPassword)
        }

        $probeResult = Invoke-MorphoManagerProbe -ProbePath $probePath -Arguments $probeArgs
        $probeExitCode = [int]$probeResult.exit_code
        $probeLines = $probeResult.lines
        $probeMap = $probeResult.map

        $health.server_hostname = [string](Get-HashtableValue -Hashtable $probeMap -Key "server_hostname")
        $health.server_port = if ($probeMap.ContainsKey("server_port")) { [int]$probeMap["server_port"] } else { 0 }
        $health.webservice_protocol = [string](Get-HashtableValue -Hashtable $probeMap -Key "webservice_protocol")
        $health.tls_mode = [string](Get-HashtableValue -Hashtable $probeMap -Key "tls_mode")
        $health.cached_credential_bytes = if ($probeMap.ContainsKey("cached_credential_bytes")) { [int]$probeMap["cached_credential_bytes"] } else { 0 }
        $health.session_client_created = [string](Get-HashtableValue -Hashtable $probeMap -Key "session_client_created") -eq "True"
        $health.function_client_created = [string](Get-HashtableValue -Hashtable $probeMap -Key "function_client_created") -eq "True"
        $health.credential_source = [string](Get-HashtableValue -Hashtable $probeMap -Key "credential_source" -Default "none")
        $health.login_status = [string](Get-HashtableValue -Hashtable $probeMap -Key "login_status" -Default "not_attempted")
        $health.session = @{
            is_client_administrator = [string](Get-HashtableValue -Hashtable $probeMap -Key "is_client_administrator") -eq "True"
            is_server_administrator = [string](Get-HashtableValue -Hashtable $probeMap -Key "is_server_administrator") -eq "True"
            issued_to_consumer_identifier = [string](Get-HashtableValue -Hashtable $probeMap -Key "issued_to_consumer_identifier")
            issued_to_instance_application_name = [string](Get-HashtableValue -Hashtable $probeMap -Key "issued_to_instance_application_name")
        }
        $health.inventory = @{
            number_terminal_secured = if ($probeMap.ContainsKey("inventory_terminal_secured")) { [int]$probeMap["inventory_terminal_secured"] } else { 0 }
            number_terminal_not_secured = if ($probeMap.ContainsKey("inventory_terminal_not_secured")) { [int]$probeMap["inventory_terminal_not_secured"] } else { 0 }
            number_terminal_not_compatible = if ($probeMap.ContainsKey("inventory_terminal_not_compatible")) { [int]$probeMap["inventory_terminal_not_compatible"] } else { 0 }
            exception = [string](Get-HashtableValue -Hashtable $probeMap -Key "inventory_exception")
        }

        if ($probeExitCode -ne 0) {
            $probeError = if ($probeMap.ContainsKey("login_error")) {
                [string]$probeMap["login_error"]
            } elseif ($probeMap.ContainsKey("config_error")) {
                [string]$probeMap["config_error"]
            } elseif ($probeMap.ContainsKey("fatal")) {
                [string]$probeMap["fatal"]
            } else {
                ($probeLines | ForEach-Object { [string]$_ }) -join " | "
            }
            if ($probeError -like "*Access denied*") {
                $health.status = "admin_required"
                $health.details = "Run the MorphoManager health check or attendance app from an Administrator PowerShell session to query the local MorphoManager SDK."
            } else {
                $health.status = "error"
                $health.details = [string]$probeError
            }
            return $health
        }

        if ($health.login_status -eq "success") {
            $listResult = Invoke-MorphoManagerProbe `
                -ProbePath $probePath `
                -Arguments @("list-biometric-devices", $ManagerUsername, $ManagerPassword)
            $listMap = $listResult.map
            if ([int]$listResult.exit_code -eq 0) {
                $health.registered_device_count = if ($listMap.ContainsKey("item_count")) { [int]$listMap["item_count"] } else { 0 }
                $devices = @()
                for ($i = 0; $i -lt $health.registered_device_count; $i++) {
                    $devices += @{
                        name = [string](Get-HashtableValue -Hashtable $listMap -Key ("device[" + $i + "].Name"))
                        serial_number = [string](Get-HashtableValue -Hashtable $listMap -Key ("device[" + $i + "].SerialNumber"))
                        device_type = [string](Get-HashtableValue -Hashtable $listMap -Key ("device[" + $i + "].DeviceType"))
                        hardware_family = [string](Get-HashtableValue -Hashtable $listMap -Key ("device[" + $i + "].HardwareFamily"))
                        status = [string](Get-HashtableValue -Hashtable $listMap -Key ("device[" + $i + "].Status"))
                        host = [string](Get-HashtableValue -Hashtable $listMap -Key ("device[" + $i + "].IPAddressHostname"))
                        port = [string](Get-HashtableValue -Hashtable $listMap -Key ("device[" + $i + "].Port"))
                        error_message = [string](Get-HashtableValue -Hashtable $listMap -Key ("device[" + $i + "].ErrorMessage"))
                    }
                }
                $health.registered_devices = $devices
            }
        }

        switch ($health.login_status) {
            "success" {
                if ($health.registered_device_count -gt 0) {
                    $health.status = "authenticated"
                    $health.details = "MorphoManager session login succeeded."
                } else {
                    $health.status = "no_registered_devices"
                    $health.details = "MorphoManager login succeeded, but no biometric devices are registered in MorphoManager yet."
                }
            }
            "needs_credentials" {
                $health.status = "needs_credentials"
                $health.details = "Both MorphoManager username and password are required."
            }
            default {
                if ($health.cached_credential_bytes -gt 0) {
                    $health.status = "cached_credentials_present"
                    $health.details = "MorphoManager has cached credentials, but the bridge does not reuse them automatically yet."
                } else {
                    $health.status = "needs_credentials"
                    $health.details = "MorphoManager is reachable, but the bridge needs ATTENDANCE_FINGERPRINT_MORPHO_USERNAME and ATTENDANCE_FINGERPRINT_MORPHO_PASSWORD."
                }
            }
        }
        return $health
    } catch {
        $health.status = "error"
        $health.login_status = if ($health.credential_source -eq "explicit") { "failed" } else { $health.login_status }
        $health.details = $_.Exception.Message
        return $health
    }
}

try {
    $payload = ConvertTo-BridgeHashtable -Value (ConvertFrom-Json $PayloadJson)
    $sdkRoot = [System.IO.Path]::GetFullPath($SdkDir)
    if (-not (Test-Path -LiteralPath $sdkRoot)) {
        throw "The Morpho SDK directory does not exist: $sdkRoot"
    }

    Enable-MorphoSdkProcessPath -SdkRoot $sdkRoot

    $requiredAssemblies = @(
        "ID1.MorphoSmart.SharedLibrary.dll",
        "ID1.MorphoSmartCpp.dll",
        "ID1.MorphoSmart.dll"
    )
    foreach ($fileName in $requiredAssemblies) {
        $fullPath = Join-Path $sdkRoot $fileName
        if (-not (Test-Path -LiteralPath $fullPath)) {
            throw "Required Morpho SDK file is missing: $fullPath"
        }
    }

    Push-Location $sdkRoot
    try {
        foreach ($fileName in $requiredAssemblies) {
            [void][System.Reflection.Assembly]::LoadFrom((Join-Path $sdkRoot $fileName))
        }
        $systemDrawingAssemblyPath = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\System.Drawing.dll"
        if (-not (Test-Path -LiteralPath $systemDrawingAssemblyPath)) {
            $systemDrawingAssemblyPath = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\System.Drawing.dll"
        }
        if (-not (Test-Path -LiteralPath $systemDrawingAssemblyPath)) {
            throw "System.Drawing.dll could not be located on this Windows installation."
        }

        Add-Type -ReferencedAssemblies @(
            (Join-Path $sdkRoot "ID1.MorphoSmart.SharedLibrary.dll"),
            (Join-Path $sdkRoot "ID1.MorphoSmartCpp.dll"),
            (Join-Path $sdkRoot "ID1.MorphoSmart.dll"),
            $systemDrawingAssemblyPath
        ) -TypeDefinition @"
using System;
using System.IO;
using System.Threading;
using ID1.MorphoSmart;

public static class MorphoBridgeHub
{
    public static ManualResetEventSlim Done = new ManualResetEventSlim(false);
    public static object Response;
    public static Exception AsyncException;
    public static string ProgressJsonPath = "";
    public static string PreviewImagePath = "";

    public static void Reset()
    {
        Done.Reset();
        Response = null;
        AsyncException = null;
    }

    private static string Escape(string value)
    {
        if (value == null) return "";
        return value.Replace("\\", "\\\\").Replace("\"", "\\\"");
    }

    private static void WriteProgress(
        string state,
        string message,
        string instruction,
        int acquisitionNumber,
        int quality,
        bool complete,
        bool success,
        string templateRef,
        object previewBitmap)
    {
        try
        {
            var previewAvailable = false;
            if (!string.IsNullOrWhiteSpace(PreviewImagePath) && previewBitmap != null)
            {
                var previewDir = Path.GetDirectoryName(PreviewImagePath);
                if (!string.IsNullOrWhiteSpace(previewDir))
                {
                    Directory.CreateDirectory(previewDir);
                }
                var saveMethod = previewBitmap.GetType().GetMethod("Save", new[] { typeof(string) });
                if (saveMethod != null)
                {
                    saveMethod.Invoke(previewBitmap, new object[] { PreviewImagePath });
                    previewAvailable = true;
                }
            }
            else if (!string.IsNullOrWhiteSpace(PreviewImagePath))
            {
                previewAvailable = File.Exists(PreviewImagePath);
            }

            if (string.IsNullOrWhiteSpace(ProgressJsonPath))
            {
                return;
            }

            var statusDir = Path.GetDirectoryName(ProgressJsonPath);
            if (!string.IsNullOrWhiteSpace(statusDir))
            {
                Directory.CreateDirectory(statusDir);
            }

            var json =
                "{"
                + "\"state\":\"" + Escape(state) + "\","
                + "\"message\":\"" + Escape(message) + "\","
                + "\"instruction\":\"" + Escape(instruction) + "\","
                + "\"acquisition_number\":" + acquisitionNumber + ","
                + "\"quality\":" + quality + ","
                + "\"complete\":" + (complete ? "true" : "false") + ","
                + "\"success\":" + (success ? "true" : "false") + ","
                + "\"template_ref\":\"" + Escape(templateRef) + "\","
                + "\"preview_available\":" + (previewAvailable ? "true" : "false") + ","
                + "\"updated_at\":\"" + DateTime.UtcNow.ToString("o") + "\""
                + "}";
            File.WriteAllText(ProgressJsonPath, json);
        }
        catch
        {
        }
    }

    public static void HandleInitException(Exception ex)
    {
        AsyncException = ex;
        WriteProgress("error", ex.Message, "", 0, 0, true, false, "", null);
        Done.Set();
    }

    public static void HandleEnumerate(EnumerateDevicesMorphoSmartResponse response)
    {
        Response = response;
        if (response != null && response.OperationComplete)
        {
            Done.Set();
        }
    }

    public static void HandleGeneric(MorphoSmartResponse response)
    {
        Response = response;
        if (response != null && response.OperationComplete)
        {
            Done.Set();
        }
    }

    public static void HandleTemplateCapture(TemplateCaptureMorphoSmartResponse response)
    {
        object preview = response != null ? (object)(response.BMP ?? response.DisplayBMP1 ?? response.DisplayBMP2 ?? response.DisplayBMP3 ?? response.DisplayBMP4) : null;
        WriteProgress(
            response != null ? (response.OperationComplete ? "processing" : "capturing") : "capturing",
            response != null ? response.Status.ToString() : "Capturing",
            response != null ? response.Instruction.ToString() : "",
            response != null ? response.AcquisitionNumber : 0,
            response != null ? response.Quality1 : 0,
            response != null && response.OperationComplete,
            false,
            "",
            preview
        );
        Response = response;
        if (response != null && response.OperationComplete)
        {
            Done.Set();
        }
    }

    public static void HandleGetUserMonikers(GetUserMonikersMorphoSmartResponse response)
    {
        Response = response;
        if (response != null && response.OperationComplete)
        {
            Done.Set();
        }
    }

    public static void HandleSampleCaptureAndMatch(SampleCaptureAndMatchMorphoSmartResponse response)
    {
        Response = response;
        if (response != null && response.OperationComplete)
        {
            Done.Set();
        }
    }

    public static void HandleCaptureAndVerify(CaptureAndVerifyMorphoSmartResponse response)
    {
        Response = response;
        if (response != null && response.OperationComplete)
        {
            Done.Set();
        }
    }
}
"@

        $initDelegate = New-BridgeDelegate `
            -DelegateType ([ID1.MorphoSmart.MorphoSmartExceptionHandler]) `
            -MethodName "HandleInitException"
        [MorphoBridgeHub]::ProgressJsonPath = $ProgressJsonPath
        [MorphoBridgeHub]::PreviewImagePath = $PreviewImagePath
        [ID1.MorphoSmart.MorphoSmart]::InitializeLibrary($initDelegate)

        $fingerEnum = [System.Enum]::Parse([ID1.MorphoSmart.Finger], $Finger, $true)
        $thresholdEnum = [System.Enum]::Parse([ID1.MorphoSmart.MatchThresholdEnum], $Threshold, $true)

        switch ($Action) {
            "health" {
                $usbService = Get-Service -Name "MSO_SpUsb_Service" -ErrorAction SilentlyContinue
                $managerService = Get-Service -Name "MorphoManager" -ErrorAction SilentlyContinue
                $deviceStatus = "error"
                $deviceDetails = ""
                $deviceSerial = $DeviceSerial
                $deviceDescription = ""
                $enumerationStatus = ""
                $enumerationError = ""
                $deviceCount = 0
                $managerHealth = Get-MorphoManagerHealth -SdkRoot $sdkRoot

                try {
                    $enumeration = Invoke-MorphoEnumeration
                    $enumerationStatus = $enumeration.Status.ToString()
                    $enumerationError = $enumeration.ErrorMessage
                    $deviceCount = if ($null -ne $enumeration.Devices) { $enumeration.Devices.Count } else { 0 }

                    if ($enumeration.Status.ToString() -eq "Success" -and $deviceCount -gt 0) {
                        $device = $enumeration.Devices[0]
                        $deviceSerial = $device.SerialNumber
                        $deviceDescription = $device.Description
                        $deviceStatus = "ready"
                        $deviceDetails = "Morpho SDK reached the scanner successfully."
                    } else {
                        $deviceDetails = $enumeration.ErrorMessage
                    }
                } catch {
                    $deviceDetails = $_.Exception.Message
                }

                $overallStatus = $deviceStatus
                $overallDetails = $deviceDetails
                if ($deviceStatus -eq "no_enrolled_users") {
                    $overallStatus = "no_enrolled_users"
                    $overallDetails = $deviceDetails
                } elseif ($deviceStatus -ne "ready") {
                    switch ($managerHealth.status) {
                        "authenticated" {
                            $overallStatus = "session_ready"
                            $overallDetails = "MorphoManager login succeeded. The direct SDK still cannot open the scanner, so capture should move through the MorphoManager session path."
                        }
                        "needs_credentials" {
                            $overallStatus = "needs_credentials"
                            $overallDetails = $managerHealth.details
                        }
                        "no_registered_devices" {
                            $overallStatus = "no_registered_devices"
                            $overallDetails = $managerHealth.details
                        }
                        "cached_credentials_present" {
                            $overallStatus = "cached_credentials_present"
                            $overallDetails = $managerHealth.details
                        }
                        "admin_required" {
                            $overallStatus = "admin_required"
                            $overallDetails = $managerHealth.details
                        }
                        default {
                            if (-not [string]::IsNullOrWhiteSpace($managerHealth.details)) {
                                $overallDetails = $managerHealth.details
                            }
                        }
                    }
                }

                Exit-WithBridgeJson @{
                    backend = "morphosmart"
                    status = $overallStatus
                    details = $overallDetails
                    sdk_dir = $sdkRoot
                    device_serial = $deviceSerial
                    device_description = $deviceDescription
                    enumerate_status = $enumerationStatus
                    enumerate_error = $enumerationError
                    device_count = $deviceCount
                    direct_sdk = @{
                        status = $deviceStatus
                        details = $deviceDetails
                        enumerate_status = $enumerationStatus
                        enumerate_error = $enumerationError
                        device_count = $deviceCount
                        device_serial = $deviceSerial
                        device_description = $deviceDescription
                    }
                    morphomanager = $managerHealth
                    services = @{
                        mso_usb_service = if ($usbService) { $usbService.Status.ToString() } else { "NotInstalled" }
                        morphomanager_service = if ($managerService) { $managerService.Status.ToString() } else { "NotInstalled" }
                    }
                }
            }

            "enroll" {
                $staffCode = [string]$payload.staff_code
                if ([string]::IsNullOrWhiteSpace($staffCode)) {
                    throw "Enrollment requires a staff_code value."
                }

                Write-EnrollmentProgress `
                    -State "starting" `
                    -Message "Preparing the scanner for fingerprint enrollment..." `
                    -Instruction "NoFingerDetected" `
                    -Complete:$false

                $device = Resolve-MorphoDevice
                $captureResponse = Invoke-MorphoTemplateCapture `
                    -TargetSerial $device.serial `
                    -TargetFinger $fingerEnum
                if ($captureResponse.Status.ToString() -eq "VerificationFailed") {
                    throw "The scanner saw the finger, but the enrollment samples did not match closely enough. Use the same finger each time, keep it flat, and follow the on-screen RemoveFinger and OK prompts."
                }
                Assert-MorphoSuccess -Response $captureResponse -OperationName "Template capture"

                $templateBytes = $captureResponse.MorphoPKCompV2
                if ($null -eq $templateBytes -or $templateBytes.Length -lt 1) {
                    throw "Template capture completed, but the SDK did not return a MorphoPKCompV2 template."
                }

                $userId = [guid]::NewGuid()

                Write-EnrollmentProgress `
                    -State "completed" `
                    -Message "MorphoSmart enrollment completed successfully." `
                    -Instruction "Completed" `
                    -AcquisitionNumber ([int]$captureResponse.AcquisitionNumber) `
                    -Quality ([int]$captureResponse.EnrolmentScore) `
                    -Complete:$true `
                    -Success:$true `
                    -TemplateRef $userId.ToString()

                Exit-WithBridgeJson @{
                    template_ref = $userId.ToString()
                    quality_score = [int]$captureResponse.EnrolmentScore
                    message = "MorphoSmart enrollment completed."
                    template_format = "MorphoPKCompV2"
                    template_data_base64 = [System.Convert]::ToBase64String($templateBytes)
                    device_serial = $device.serial
                    device_description = $device.description
                }
            }

            "identify" {
                $device = Resolve-MorphoDevice
                $candidates = @($payload.candidates)
                $referenceUsers = New-MorphoReferenceUsers -Candidates $candidates
                if ($referenceUsers.Count -lt 1) {
                    throw "No enrolled MorphoSmart templates are stored in the attendance app yet."
                }

                $matchResponse = Invoke-MorphoCaptureAndVerify `
                    -TargetSerial $device.serial `
                    -TargetThreshold $thresholdEnum `
                    -ReferenceUsers $referenceUsers

                $statusName = $matchResponse.Status.ToString()
                if ($statusName -eq "VerificationFailed") {
                    Exit-WithBridgeJson @{
                        matched = $false
                        message = "Fingerprint did not match any MorphoSmart user."
                        device_serial = $device.serial
                    }
                }

                Assert-MorphoSuccess -Response $matchResponse -OperationName "Capture and verify"
                $matchIndex = if ($null -ne $matchResponse.MatchIndex) { [int]$matchResponse.MatchIndex } else { -1 }
                if ($matchIndex -lt 0 -or $matchIndex -ge $candidates.Count) {
                    Exit-WithBridgeJson @{
                        matched = $false
                        message = "Fingerprint did not match any MorphoSmart user."
                        device_serial = $device.serial
                    }
                }
                $matchedCandidate = ConvertTo-BridgeHashtable -Value $candidates[$matchIndex]
                $matchedTemplateRef = [string](Get-HashtableValue -Hashtable $matchedCandidate -Key "template_ref")

                Exit-WithBridgeJson @{
                    matched = $true
                    template_ref = $matchedTemplateRef
                    confidence = [int]$matchResponse.MatchScore
                    message = "MorphoSmart match found."
                    device_serial = $device.serial
                }
            }

            "delete" {
                $templateRef = [string]$payload.template_ref
                if ([string]::IsNullOrWhiteSpace($templateRef)) {
                    throw "Delete requires a template_ref value."
                }

                Exit-WithBridgeJson @{
                    deleted = $true
                    template_ref = $templateRef
                    message = "MorphoSmart template removed from the attendance app."
                }
            }
        }
    } finally {
        Pop-Location
    }
} catch {
    $resolvedMessage = Resolve-BridgeErrorMessage -Operation $Action -Message $_.Exception.Message
    if ($Action -eq "enroll") {
        Write-EnrollmentProgress `
            -State "error" `
            -Message $resolvedMessage `
            -Complete:$true `
            -Success:$false
    }
    Exit-WithBridgeJson @{
        backend = "morphosmart"
        status = "error"
        error = $resolvedMessage
        details = $resolvedMessage
    } 1
}
