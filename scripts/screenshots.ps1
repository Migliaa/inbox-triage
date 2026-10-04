# Takes the lab screenshots used in the documentation, with headless Edge.
# Both servers must be running (mock API on 8099, lab on 8000) and the model loaded.
#   powershell -File scripts/screenshots.ps1

$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$out = Join-Path $PSScriptRoot "..\docs\img"
New-Item -ItemType Directory -Force $out | Out-Null

$shots = [ordered]@{
    "lab-e008-uncertain"      = "http://127.0.0.1:8000/?email=e-008"
    "lab-e007-injection"      = "http://127.0.0.1:8000/?email=e-007"
    "lab-e003-actions"        = "http://127.0.0.1:8000/?email=e-003"
    "lab-p01-hidden-injection" = "http://127.0.0.1:8000/?probe=p-01"
    "lab-p02-quoted-attack"   = "http://127.0.0.1:8000/?probe=p-02"
    "lab-p12-missed-injection" = "http://127.0.0.1:8000/?probe=p-12"
    "api-docs"                = "http://127.0.0.1:8000/docs"
}

foreach ($name in $shots.Keys) {
    $file = Join-Path (Resolve-Path $out) "$name.png"
    Start-Process -FilePath $edge -Wait -ArgumentList @(
        "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--user-data-dir=$env:TEMP\edge-shot", "--window-size=1280,1300",
        "--virtual-time-budget=15000", "--screenshot=`"$file`"", $shots[$name]
    )
    Write-Host "$name.png"
}
