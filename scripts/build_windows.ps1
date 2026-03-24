Param(
    [string]$Python = "venv\Scripts\python.exe",
    [string]$ArtifactRoot = "artifacts\windows",
    [string]$PublishRoot = "C:\MeetingTranslatorNetwork_Setup\Windows"
)

$ErrorActionPreference = "Stop"

Write-Host "Building MeetingTranslatorNetwork for Windows..."

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $repoRoot
try {
    $pythonPath = if ([System.IO.Path]::IsPathRooted($Python)) { $Python } else { Join-Path $repoRoot $Python }
    if (!(Test-Path $pythonPath)) {
        throw "Python introuvable: $pythonPath"
    }

    $artifactRootPath = if ([System.IO.Path]::IsPathRooted($ArtifactRoot)) { $ArtifactRoot } else { Join-Path $repoRoot $ArtifactRoot }
    $buildPath = Join-Path $artifactRootPath "build"
    $distPath = Join-Path $artifactRootPath "dist"
    $specPath = Join-Path $artifactRootPath "spec"
    $installerPath = Join-Path $artifactRootPath "installer"

    New-Item -ItemType Directory -Force -Path $buildPath | Out-Null
    New-Item -ItemType Directory -Force -Path $distPath | Out-Null
    New-Item -ItemType Directory -Force -Path $specPath | Out-Null
    New-Item -ItemType Directory -Force -Path $installerPath | Out-Null

    $mainPath = (Resolve-Path (Join-Path $repoRoot "src\main.py")).Path
    $srcPath = (Resolve-Path (Join-Path $repoRoot "src")).Path
    $stylePath = (Resolve-Path (Join-Path $repoRoot "src\ui\style.qss")).Path
    $assetsPath = (Resolve-Path (Join-Path $repoRoot "assets")).Path
    $issPath = (Resolve-Path (Join-Path $repoRoot "packaging\windows\MeetingTranslatorNetwork.iss")).Path
    $brandingPath = Join-Path $assetsPath "branding"
    $appIcoPath = Join-Path $brandingPath "windows\app.ico"
    $setupIcoPath = Join-Path $brandingPath "windows\setup.ico"
    $wizardImagePath = Join-Path $brandingPath "windows\wizard.bmp"
    $wizardSmallImagePath = Join-Path $brandingPath "windows\wizard_small.bmp"

    # Always regenerate spec to avoid stale relative paths under artifacts/windows/spec.
    $specFile = Join-Path $specPath "MeetingTranslatorNetwork.spec"
    if (Test-Path $specFile) {
        Remove-Item -Force $specFile
    }

    & $pythonPath -m pip install --upgrade pip
    & $pythonPath -m pip install -r requirements.txt
    & $pythonPath -m pip install pyinstaller

    $pyArgs = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name", "MeetingTranslatorNetwork",
        "--workpath", "$buildPath",
        "--distpath", "$distPath",
        "--specpath", "$specPath",
        "--paths", "$srcPath",
        "--add-data", "$stylePath;ui",
        "--add-data", "$assetsPath;assets"
    )
    if (Test-Path $appIcoPath) {
        $pyArgs += @("--icon", "$appIcoPath")
        Write-Host "Branding app icon detecte: $appIcoPath"
    }
    $pyArgs += "$mainPath"
    & $pythonPath @pyArgs

    Write-Host "Build termine: $distPath\MeetingTranslatorNetwork\MeetingTranslatorNetwork.exe"

    if (Get-Command iscc.exe -ErrorAction SilentlyContinue) {
        $isccPath = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
    } else {
        $candidates = @(
            "C:\Users\$env:USERNAME\AppData\Local\Programs\Inno Setup 6\ISCC.exe",
            "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            "C:\Program Files\Inno Setup 6\ISCC.exe"
        )
        $isccPath = $null
        foreach ($c in $candidates) {
            if (Test-Path $c) {
                $isccPath = $c
                break
            }
        }
    }

    if ($isccPath) {
        Write-Host "Inno Setup detecte, generation de l'installateur..."
        $isccArgs = @(
            "/DMyOutputDir=$installerPath",
            "/DMyDistDir=$distPath\MeetingTranslatorNetwork"
        )
        if (Test-Path $setupIcoPath) {
            $isccArgs += "/DMySetupIconFile=$setupIcoPath"
            Write-Host "Branding setup icon detecte: $setupIcoPath"
        }
        if (Test-Path $wizardImagePath) {
            $isccArgs += "/DMyWizardImageFile=$wizardImagePath"
            Write-Host "Branding wizard image detectee: $wizardImagePath"
        }
        if (Test-Path $wizardSmallImagePath) {
            $isccArgs += "/DMyWizardSmallImageFile=$wizardSmallImagePath"
            Write-Host "Branding wizard small image detectee: $wizardSmallImagePath"
        }
        $isccArgs += "$issPath"
        & $isccPath @isccArgs
        Write-Host "Installateur genere dans $installerPath"
    } else {
        Write-Host "Inno Setup non detecte (iscc.exe). EXE standalone disponible dans $distPath."
    }

    if ($PublishRoot) {
        New-Item -ItemType Directory -Force -Path $PublishRoot | Out-Null

        $publishAppDir = Join-Path $PublishRoot "MeetingTranslatorNetwork"
        $publishSetupExe = Join-Path $PublishRoot "MeetingTranslatorNetwork-Setup-v1.exe"
        $publishPortableZip = Join-Path $PublishRoot "MeetingTranslatorNetwork-Windows-Portable.zip"

        New-Item -ItemType Directory -Force -Path $publishAppDir | Out-Null
        & robocopy "$distPath\MeetingTranslatorNetwork" "$publishAppDir" /E /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null

        $builtSetupExe = Join-Path $installerPath "MeetingTranslatorNetwork-Setup-v1.exe"
        if (Test-Path $builtSetupExe) {
            Copy-Item -Force $builtSetupExe $publishSetupExe
        }

        if (Test-Path $publishPortableZip) {
            Remove-Item -Force $publishPortableZip
        }
        Compress-Archive -Path "$distPath\MeetingTranslatorNetwork\*" -DestinationPath $publishPortableZip -CompressionLevel Optimal

        Write-Host "Livrables copies vers $PublishRoot"
    }
}
finally {
    Pop-Location
}
