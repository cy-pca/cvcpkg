# recipes/grpc/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

# Pre-download third_party archives that grpc's CMakeLists.txt would otherwise
# fetch via cmake file(DOWNLOAD) which has no retries and fails frequently in CI.
function Invoke-GrpcFetchArchive {
    param(
        [string]$Dest,
        [string]$Url,
        [string]$Sha256,
        [string]$StripDir
    )
    if (Test-Path $Dest) { return }
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $archivePath = Join-Path $tmp 'archive'
    Write-Host "cvcpkg: downloading $(Split-Path -Leaf $Dest) ..."
    $retries = 5
    for ($i = 1; $i -le $retries; $i++) {
        try {
            Invoke-WebRequest -Uri $Url -OutFile $archivePath -UseBasicParsing
            break
        } catch {
            if ($i -eq $retries) { throw }
            Write-Host "  retry $i/$retries ..."
            Start-Sleep -Seconds 3
        }
    }
    $actual = (Get-FileHash -Path $archivePath -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $Sha256) {
        Remove-Item -Recurse -Force $tmp
        throw "sha256 mismatch for $Url (expected $Sha256, got $actual)"
    }
    $extractDir = Join-Path $tmp 'extract'
    New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
    if ($Url -like '*.zip') {
        Expand-Archive -Path $archivePath -DestinationPath $extractDir
    } else {
        # GNU tar treats drive-letter prefixes (C:) as remote host references.
        # Use --force-local to disable this interpretation on Windows.
        tar --force-local -xzf ($archivePath -replace '\\','/') -C ($extractDir -replace '\\','/')
    }
    $parentDir = Split-Path -Parent $Dest
    if (-not (Test-Path $parentDir)) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
    }
    Move-Item -Path (Join-Path $extractDir $StripDir) -Destination $Dest
    Remove-Item -Recurse -Force $tmp
}

$srcDir = $env:CVC_SOURCE_DIR

Invoke-GrpcFetchArchive `
    -Dest "$srcDir\third_party\envoy-api" `
    -Url 'https://github.com/envoyproxy/data-plane-api/archive/f8b75d1efa92bbf534596a013d9ca5873f79dd30.tar.gz' `
    -Sha256 'e525a6fb6e6ed3eef1eec6bef3da9b5708e471f0f9335a7604df14a4b386231e' `
    -StripDir 'data-plane-api-f8b75d1efa92bbf534596a013d9ca5873f79dd30'

Invoke-GrpcFetchArchive `
    -Dest "$srcDir\third_party\googleapis" `
    -Url 'https://github.com/googleapis/googleapis/archive/fe8ba054ad4f7eca946c2d14a63c3f07c0b586a0.tar.gz' `
    -Sha256 '0513f0f40af63bd05dc789cacc334ab6cec27cc89db596557cb2dfe8919463e4' `
    -StripDir 'googleapis-fe8ba054ad4f7eca946c2d14a63c3f07c0b586a0'

Invoke-GrpcFetchArchive `
    -Dest "$srcDir\third_party\opencensus-proto\src" `
    -Url 'https://github.com/census-instrumentation/opencensus-proto/archive/v0.3.0.tar.gz' `
    -Sha256 'b7e13f0b4259e80c3070b583c2f39e53153085a6918718b1c710caf7037572b0' `
    -StripDir 'opencensus-proto-0.3.0/src'

Invoke-GrpcFetchArchive `
    -Dest "$srcDir\third_party\protoc-gen-validate" `
    -Url 'https://github.com/bufbuild/protoc-gen-validate/archive/refs/tags/v1.0.4.zip' `
    -Sha256 '9372f9ecde8fbadf83c8c7de3dbb49b11067aa26fb608c501106d0b4bf06c28f' `
    -StripDir 'protoc-gen-validate-1.0.4'

Invoke-GrpcFetchArchive `
    -Dest "$srcDir\third_party\xds" `
    -Url 'https://github.com/cncf/xds/archive/3a472e524827f72d1ad621c4983dd5af54c46776.tar.gz' `
    -Sha256 'dc305e20c9fa80822322271b50aa2ffa917bf4fd3973bcec52bfc28dc32c5927' `
    -StripDir 'xds-3a472e524827f72d1ad621c4983dd5af54c46776'

Invoke-CvcCMakeBuild @(
    '-DgRPC_BUILD_TESTS=OFF',
    '-DgRPC_BUILD_CSHARP_EXT=OFF',
    '-DgRPC_BUILD_GRPC_CPP_PLUGIN=ON',
    '-DgRPC_BUILD_GRPC_CSHARP_PLUGIN=OFF',
    '-DgRPC_BUILD_GRPC_NODE_PLUGIN=OFF',
    '-DgRPC_BUILD_GRPC_OBJECTIVE_C_PLUGIN=OFF',
    '-DgRPC_BUILD_GRPC_PHP_PLUGIN=OFF',
    '-DgRPC_BUILD_GRPC_PYTHON_PLUGIN=ON',
    '-DgRPC_BUILD_GRPC_RUBY_PLUGIN=OFF',
    '-DgRPC_ABSL_PROVIDER=package',
    '-DgRPC_CARES_PROVIDER=package',
    '-DgRPC_PROTOBUF_PROVIDER=package',
    '-DgRPC_RE2_PROVIDER=package',
    '-DgRPC_SSL_PROVIDER=package',
    '-DgRPC_ZLIB_PROVIDER=package',
    '-DCMAKE_CXX_STANDARD=17',
    # gRPC's upb sub-libraries lack proper DLL symbol exports on
    # Windows (LNK2019 for upb_alloc_global etc.).  Build the entire
    # protobuf ecosystem as static libs -- the .lib files still use
    # /MD (dynamic CRT) so they link cleanly into shared consumers.
    '-DBUILD_SHARED_LIBS=OFF'
)
