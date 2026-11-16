function Get-TelemetryFieldValue {
    param(
        [Parameter(Mandatory = $true)] $Record,
        [Parameter(Mandatory = $true)] [string] $Name
    )

    if ($null -eq $Record) { return $null }
    if ($Record -is [System.Collections.IDictionary]) {
        foreach ($key in $Record.Keys) {
            if ([string]$key -ieq $Name) { return $Record[$key] }
        }
    } else {
        foreach ($property in $Record.PSObject.Properties) {
            if ($property.Name -ieq $Name) { return $property.Value }
        }
    }

    foreach ($containerName in @("attributes", "raw")) {
        $container = $null
        if ($Record -is [System.Collections.IDictionary]) {
            foreach ($key in $Record.Keys) {
                if ([string]$key -ieq $containerName) { $container = $Record[$key]; break }
            }
        } else {
            foreach ($property in $Record.PSObject.Properties) {
                if ($property.Name -ieq $containerName) { $container = $property.Value; break }
            }
        }
        if ($null -ne $container) {
            $nestedValue = Get-TelemetryFieldValue -Record $container -Name $Name
            if ($null -ne $nestedValue) { return $nestedValue }
        }
    }
    return $null
}

function Test-InventoryDnsEvidence {
    param([Parameter(Mandatory = $true)] $Record)

    $dependencyHost = ([string](Get-TelemetryFieldValue -Record $Record -Name "dependency_host")).Trim()
    $errorReason = ([string](Get-TelemetryFieldValue -Record $Record -Name "error_reason")) -replace "\s+", " "
    $message = ([string](Get-TelemetryFieldValue -Record $Record -Name "message")) -replace "\s+", " "
    return ($dependencyHost -eq "inventory-v2" -or
        $errorReason -match "inventory-v2|Name or service not known|No address associated with hostname" -or
        $message -match "inventory-v2|Name or service not known|No address associated with hostname")
}

Export-ModuleMember -Function Get-TelemetryFieldValue, Test-InventoryDnsEvidence
